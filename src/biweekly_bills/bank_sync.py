from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any

from .bank_data import cycle_for_day, match_rule
from .bill_rules import BILL_RULES
from .database import Database, utc_now
from .local_config import load_local_config, update_local_config
from .plaid_sdk import build_client, get_balance, get_item, sync_transactions
from .secure_store import load_credentials, require_access_token
from .settings import load_settings


@dataclass(frozen=True)
class BankSyncReport:
    environment: str
    account_count: int
    added_count: int
    modified_count: int
    removed_count: int
    transaction_count: int
    update_status: str
    last_sync_at: str
    item_id: str | None
    item_error: object | None
    auto_matched_count: int = 0
    auto_review_count: int = 0
    auto_internal_transfer_count: int = 0


SandboxSyncReport = BankSyncReport


def _cents(value: Any) -> int | None:
    if value is None:
        return None
    return int(round(float(value) * 100))


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)[:10]


def connection_status(environment: str) -> dict[str, Any]:
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")
    settings = load_settings(require_keys=False)
    credentials = load_credentials(env)
    return {
        "environment": env,
        "configured_environment": settings.environment,
        "keys_configured": bool(settings.client_id and settings.secret),
        "credential_present": bool(credentials.get("access_token")),
        "item_id": credentials.get("item_id"),
    }


def sandbox_connection_status() -> dict[str, Any]:
    return connection_status("sandbox")


def production_connection_status() -> dict[str, Any]:
    return connection_status("production")


def set_bills_account(
    database: Database,
    environment: str,
    plaid_account_id: str,
) -> None:
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")
    if env == "production":
        from .production_readiness import assert_production_sync_allowed

        assert_production_sync_allowed(database)
    database.set_bills_checking(env, plaid_account_id)
    update_local_config(env, bills_account_id=plaid_account_id)


def set_sandbox_bills_account(database: Database, plaid_account_id: str) -> None:
    set_bills_account(database, "sandbox", plaid_account_id)


def _sync_environment_to_sqlite(
    database: Database,
    environment: str,
    *,
    persist_local_cursor: bool = True,
) -> BankSyncReport:
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")

    settings = load_settings()
    if settings.environment != env:
        raise RuntimeError(
            f"Plaid environment mismatch: app is configured for {settings.environment}, "
            f"but {env} sync was requested."
        )

    expected_item_id: str | None = None
    if env == "production":
        # Lazy import avoids a circular dependency: Sandbox validation imports
        # bank_sync, while Production readiness imports Sandbox validation.
        from .production_readiness import assert_production_sync_allowed

        readiness = assert_production_sync_allowed(database)
        expected_item_id = readiness.production_item_id

    access_token = require_access_token(env)
    client = build_client(settings)
    local = load_local_config(env)
    now = utc_now()

    item_payload = get_item(client, access_token)
    item = item_payload.get("item") or {}
    item_error = item.get("error")
    item_id = str(item.get("item_id") or "") or None

    if expected_item_id and item_id != expected_item_id:
        raise RuntimeError(
            "Plaid returned a Production Item ID that does not match the locked "
            "persistent Item. No account or transaction data was written."
        )

    if item_error:
        label = "Production" if env == "production" else "Sandbox"
        raise RuntimeError(
            f"{label} Plaid Item needs repair before sync. "
            "Use 'biweekly-bills update-link' to repair this same Item in Update Mode. "
            "Do not run Initial Link again. "
            f"Plaid Item error: {item_error}"
        )

    balance_payload = get_balance(client, access_token)
    accounts = balance_payload.get("accounts", []) or []

    # Item identity and health are verified before the first SQLite write.
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        if not account_id:
            continue
        balances = account.get("balances") or {}
        database.upsert_bank_account(
            environment=env,
            plaid_account_id=account_id,
            name=str(account.get("name") or account.get("official_name") or "(unnamed account)"),
            mask=str(account.get("mask") or "") or None,
            account_type=str(account.get("type") or "") or None,
            account_subtype=str(account.get("subtype") or "") or None,
            current_balance_cents=_cents(balances.get("current")),
            available_balance_cents=_cents(balances.get("available")),
            last_synced_at=now,
        )

    selected = str(local.get("bills_account_id") or "")
    if selected and any(str(a.get("account_id") or "") == selected for a in accounts):
        database.set_bills_checking(env, selected)

    state = database.get_sync_state(env)
    existing_transactions = database.list_bank_transactions(env, limit=1)

    cursor = None
    if state is not None:
        cursor = str(state["transaction_cursor"] or "") or None

    if not cursor and existing_transactions:
        cursor = str(local.get("transactions_cursor") or "") or None

    result = sync_transactions(client, access_token, cursor=cursor)

    for tx in list(result.get("added", [])) + list(result.get("modified", [])):
        transaction_id = str(tx.get("transaction_id") or "")
        if not transaction_id:
            continue
        database.upsert_bank_transaction(
            environment=env,
            plaid_transaction_id=transaction_id,
            plaid_account_id=str(tx.get("account_id") or "") or None,
            posted_date=_iso(tx.get("date")),
            authorized_date=_iso(tx.get("authorized_date")),
            merchant_name=str(tx.get("merchant_name") or "") or None,
            name=str(tx.get("name") or "") or None,
            amount_cents=int(round(float(tx.get("amount") or 0) * 100)),
            pending=bool(tx.get("pending")),
            raw_json=json.dumps(tx, default=str, sort_keys=True),
            last_seen_at=now,
        )

    for tombstone in result.get("removed", []):
        transaction_id = str(tombstone.get("transaction_id") or "")
        if transaction_id:
            database.delete_bank_transaction(env, transaction_id)

    next_cursor = str(result.get("next_cursor") or "") or None
    update_status = str(
        result.get("transactions_update_status")
        or "TRANSACTIONS_UPDATE_STATUS_UNKNOWN"
    )
    database.update_sync_state(
        environment=env,
        transaction_cursor=next_cursor,
        transactions_update_status=update_status,
        last_sync_at=now,
    )

    if next_cursor and persist_local_cursor:
        update_local_config(env, transactions_cursor=next_cursor)

    auto_matched_count = 0
    auto_review_count = 0
    auto_internal_transfer_count = 0
    if env == "production":
        # Import lazily to avoid a module cycle: auto_reconcile reuses the
        # conservative merchant-alias helper from this module. Sandbox sync
        # must never mutate real bill-instance Paid values.
        from .auto_reconcile import auto_reconcile_transactions

        auto_report = auto_reconcile_transactions(database, env)
        auto_matched_count = auto_report.matched_count
        auto_review_count = auto_report.review_count
        auto_internal_transfer_count = auto_report.internal_transfer_count

    transaction_count = len(database.list_bank_transactions(env, limit=100000))
    return BankSyncReport(
        environment=env,
        account_count=len(accounts),
        added_count=len(result.get("added", [])),
        modified_count=len(result.get("modified", [])),
        removed_count=len(result.get("removed", [])),
        transaction_count=transaction_count,
        update_status=update_status,
        last_sync_at=now,
        item_id=item_id,
        item_error=item_error,
        auto_matched_count=auto_matched_count,
        auto_review_count=auto_review_count,
        auto_internal_transfer_count=auto_internal_transfer_count,
    )


def sync_sandbox_to_sqlite(
    database: Database,
    *,
    persist_local_cursor: bool = True,
) -> BankSyncReport:
    return _sync_environment_to_sqlite(
        database,
        "sandbox",
        persist_local_cursor=persist_local_cursor,
    )


def sync_production_to_sqlite(
    database: Database,
    *,
    persist_local_cursor: bool = True,
) -> BankSyncReport:
    return _sync_environment_to_sqlite(
        database,
        "production",
        persist_local_cursor=persist_local_cursor,
    )


def suggested_bill_for_transaction(row: Any) -> str | None:
    """Return a conservative known-bill suggestion for one posted outflow.

    Account membership is intentionally not restricted to Bills Checking: some
    bills can be paid directly from an everyday checking account. Account role
    is handled separately by funding/transfer planning.
    """

    try:
        if int(row["pending"]):
            return None
        if int(row["amount_cents"]) <= 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None

    raw_date = row["posted_date"] or row["authorized_date"]
    if not raw_date:
        return None
    try:
        tx_date = date.fromisoformat(str(raw_date)[:10])
    except ValueError:
        return None

    tx = {
        "merchant_name": row["merchant_name"],
        "name": row["name"],
        "date": str(raw_date)[:10],
        "account_id": row["plaid_account_id"],
        "amount": int(row["amount_cents"]) / 100,
        "pending": bool(row["pending"]),
    }
    cycle = cycle_for_day(tx_date.day)
    for rule in BILL_RULES:
        if rule.cycle == cycle and match_rule(tx, rule):
            return rule.name
    return None
