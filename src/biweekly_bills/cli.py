from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import threading
import time
import webbrowser

import plaid
from werkzeug.serving import make_server

from .bank_data import cycle_for_day
from .control_panel import install_control_panel
from .database import Database, default_database_path
from .desktop_integration import install_desktop_integration
from .link_server import create_app
from .local_config import config_path, load_local_config, update_local_config
from .ods_repair import repair_openformula_namespace
from .plaid_sdk import (
    build_client,
    create_dynamic_transactions_sandbox_item,
    create_sandbox_transactions,
    get_balance,
    get_item,
    sandbox_reset_login,
    sync_transactions,
)
from .production_guard import (
    PRODUCTION_LOCK,
    acquire_initial_link_session,
    clear_initial_link_session_after_confirmation,
    mark_production_item_created,
    production_item_locked,
    release_initial_link_session,
)
from .production_readiness import (
    assert_initial_production_link_allowed,
    assert_production_recovery_allowed,
    assert_production_update_allowed,
    inspect_production_readiness,
)
from .secure_store import (
    PENDING_PRODUCTION_PATH,
    archive_sandbox_credentials,
    credentials_path,
    load_credentials,
    load_pending_production_exchange,
    preflight_store,
    recover_pending_production_credentials,
    require_access_token,
    save_credentials,
)
from .settings import PROJECT_ROOT, load_settings
from .self_test import run_self_test
from .transactions import apply_sync
from .workbook_sync import (
    apply_workbook_changes,
    choose_workbook,
    inspect_workbook_matches,
    plan_workbook_changes,
)

DATA_DIR = PROJECT_ROOT / "data"
LEGACY_TRANSACTIONS_CACHE = DATA_DIR / "transactions.json"


def transactions_cache_path(environment: str) -> Path:
    return DATA_DIR / f"transactions_{environment}.json"


def _migrate_legacy_cache_to_sandbox() -> None:
    target = transactions_cache_path("sandbox")
    if target.exists() or not LEGACY_TRANSACTIONS_CACHE.exists():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(LEGACY_TRANSACTIONS_CACHE, target)


def _parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _load_cache(environment: str) -> list[dict]:
    if environment == "sandbox":
        _migrate_legacy_cache_to_sandbox()
    path = transactions_cache_path(environment)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def _save_cache(environment: str, transactions: list[dict]) -> None:
    path = transactions_cache_path(environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(transactions, handle, indent=2, default=str)
        handle.write("\n")


def _find_account_balance(accounts: list[dict], account_id: str) -> float | None:
    for account in accounts:
        actual_id = str(account.get("account_id") or account.get("id") or "")
        if actual_id != account_id:
            continue
        balances = account.get("balances") or {}
        value = balances.get("current")
        if value is None:
            value = balances.get("available")
        return float(value) if value is not None else None
    return None


def _item_error(payload: dict) -> object | None:
    item = payload.get("item") or {}
    return item.get("error")


def _run_link_flow(settings, *, update_mode: bool) -> int:
    access_token = require_access_token(settings.environment) if update_mode else None
    completion = threading.Event()
    app = create_app(
        settings,
        update_mode=update_mode,
        access_token=access_token,
        completion_event=completion,
    )
    url = f"http://{settings.host}:{settings.port}/"

    server = make_server(settings.host, settings.port, app)
    server.timeout = 1

    def open_browser() -> None:
        time.sleep(0.7)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"Opening Plaid {'Update Mode' if update_mode else 'Link'} at {url}")
    if update_mode:
        print("This repairs the existing Item. No new access token or Item will be created.")
    else:
        print("Do not close this process until the browser reports that the Item credential was persisted.")

    try:
        while not completion.is_set():
            server.handle_request()
    finally:
        server.server_close()
    print("Plaid flow completed successfully.")
    return 0


def _readiness_database() -> Database | None:
    path = default_database_path()
    return Database(path) if path.exists() else None


def cmd_production_readiness(args: argparse.Namespace) -> int:
    report = inspect_production_readiness(_readiness_database())
    print(f"Production readiness: {report.action}")
    print(report.headline)
    for check in report.checks:
        print(f"[{check.status}] {check.name}: {check.detail}")
    if report.production_item_id:
        print(f"Persistent Production Item ID: {report.production_item_id}")
    return 2 if report.blocked else 0


def cmd_clear_production_link_reservation(args: argparse.Namespace) -> int:
    if not args.confirm_no_item_created:
        raise RuntimeError(
            "Refusing to clear the Production Link reservation without "
            "--confirm-no-item-created."
        )
    clear_initial_link_session_after_confirmation()
    print("Cleared the stale Production initial-Link reservation.")
    print("No Production credential, pending exchange, or Item lock was present.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(require_keys=False)
    creds = load_credentials(settings.environment)
    pending = load_pending_production_exchange()

    print(f"Project root: {PROJECT_ROOT}")
    print(f"Plaid environment: {settings.environment}")
    print(f"Plaid Client ID configured: {'yes' if settings.client_id else 'no'}")
    print(f"Plaid Secret configured: {'yes' if settings.secret else 'no'}")
    print(f"Persistent {settings.environment} Item credential: {'yes' if creds.get('access_token') else 'no'}")
    print(f"Credential file: {credentials_path(settings.environment)}")
    print(f"Local state file: {config_path(settings.environment)}")
    print(f"Transaction cache: {transactions_cache_path(settings.environment)}")
    print(f"Production Item lock: {'present' if production_item_locked() else 'absent'}")
    print(f"Pending Production recovery credential: {'present' if pending.get('access_token') else 'absent'}")
    print("Homebrew/Plaid CLI dependency: none")

    readiness = inspect_production_readiness(_readiness_database())
    print(f"Production next safe action: {readiness.action}")
    print(f"Production readiness: {readiness.headline}")

    if settings.environment == "production" and readiness.blocked:
        return 2

    if pending.get("access_token"):
        print("WARNING: A Production token exchange is pending recovery.")
        print("Run biweekly-bills recover-production. DO NOT create another Production Item.")
        return 2

    if settings.environment == "production" and production_item_locked() and not creds.get("access_token"):
        print("WARNING: Production lock exists but the Production credential is missing.")
        print("DO NOT create another Item. Recover the existing credential first.")
        return 2
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    settings = load_settings()
    preflight_store()

    if settings.environment == "production":
        assert_initial_production_link_allowed(_readiness_database())
        acquire_initial_link_session()
        try:
            return _run_link_flow(settings, update_mode=False)
        finally:
            release_initial_link_session()

    creds = load_credentials(settings.environment)
    if creds.get("access_token"):
        raise RuntimeError(
            f"A persisted Plaid {settings.environment} Item already exists. "
            "Initial Link will not run again."
        )
    return _run_link_flow(settings, update_mode=False)


def cmd_update_link(args: argparse.Namespace) -> int:
    settings = load_settings()
    token = require_access_token(settings.environment)
    if settings.environment == "production":
        report = assert_production_update_allowed(_readiness_database())
        item_id = str(load_credentials("production").get("item_id") or "")
        if not item_id:
            raise RuntimeError("Production credential is missing its Item ID.")
        mark_production_item_created(item_id)
    return _run_link_flow(settings, update_mode=True)


def cmd_recover_production(args: argparse.Namespace) -> int:
    assert_production_recovery_allowed(_readiness_database())
    pending = load_pending_production_exchange()
    item_id = str(pending.get("item_id") or "")
    if not item_id:
        raise RuntimeError("No pending Production Item credential exists to recover.")

    mark_production_item_created(item_id)
    recovered = recover_pending_production_credentials()
    mark_production_item_created(str(recovered["item_id"]))
    print("Recovered the existing Production Plaid credential.")
    print(f"Item ID: {recovered['item_id']}")
    print(f"Credential file: {credentials_path('production')}")
    print("No new Plaid Item was created.")
    return 0


def _reset_sandbox_local_state() -> None:
    cache = transactions_cache_path("sandbox")
    if cache.exists():
        cache.unlink()
        print(f"Cleared Sandbox transaction cache: {cache}")
    update_local_config(
        "sandbox",
        transactions_cursor=None,
        bills_account_id=None,
        plaid_item_id=None,
    )
    print("Cleared Sandbox cursor/account selection.")


def cmd_sandbox_dynamic_item(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment != "sandbox":
        raise RuntimeError("sandbox-dynamic-item is disabled outside PLAID_ENV=sandbox.")

    backup = archive_sandbox_credentials()
    if backup:
        print(f"Archived previous Sandbox credential: {backup}")
    _reset_sandbox_local_state()

    client = build_client(settings)
    exchanged = create_dynamic_transactions_sandbox_item(client)
    access_token = str(exchanged.get("access_token") or "")
    item_id = str(exchanged.get("item_id") or "")
    if not access_token or not item_id:
        raise RuntimeError("Plaid did not return a complete Sandbox Item credential.")

    save_credentials(
        access_token=access_token,
        item_id=item_id,
        environment="sandbox",
    )
    print("Created deterministic Sandbox Transactions Item.")
    print("Institution: First Platypus Bank (ins_109508)")
    print("Test user: user_transactions_dynamic")
    print(f"Item ID: {item_id}")
    print("Run biweekly-bills accounts next and select the checking/depository account.")
    return 0


def cmd_sandbox_relink(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment != "sandbox":
        raise RuntimeError("sandbox-relink is disabled outside PLAID_ENV=sandbox.")

    backup = archive_sandbox_credentials()
    if backup:
        print(f"Archived previous Sandbox credential: {backup}")

    _reset_sandbox_local_state()
    print("Opening a fresh Sandbox Link.")
    return _run_link_flow(settings, update_mode=False)


def cmd_sandbox_seed_bills(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment != "sandbox":
        raise RuntimeError("sandbox-seed-bills is disabled outside PLAID_ENV=sandbox.")

    token = require_access_token(settings.environment)
    client = build_client(settings)
    tx_date = _parse_date(args.date)

    test_transactions = [
        {"date_transacted": tx_date, "date_posted": tx_date, "amount": 213.07, "description": "VERIZON WIRELESS"},
        {"date_transacted": tx_date, "date_posted": tx_date, "amount": 120.60, "description": "COX COMMUNICATIONS"},
        {"date_transacted": tx_date, "date_posted": tx_date, "amount": 272.20, "description": "USAA INSURANCE"},
        {"date_transacted": tx_date, "date_posted": tx_date, "amount": 158.00, "description": "ACELLUS ACADEMY"},
        {"date_transacted": tx_date, "date_posted": tx_date, "amount": 126.28, "description": "MILITARY STAR CARD"},
    ]

    create_sandbox_transactions(client, token, test_transactions)
    print("Created 5 Sandbox bill transactions on the linked depository account:")
    for tx in test_transactions:
        print(f"  {tx['description']}: {tx['amount']:.2f} on {tx_date.isoformat()}")
    print("Run biweekly-bills sync again to pull and match them.")
    return 0


def cmd_sandbox_reset_login(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment != "sandbox":
        raise RuntimeError("sandbox-reset-login is disabled outside PLAID_ENV=sandbox.")

    token = require_access_token("sandbox")
    client = build_client(settings)
    result = sandbox_reset_login(client, token)
    if not result.get("reset_login"):
        raise RuntimeError("Plaid did not confirm Sandbox login reset.")

    item = get_item(client, token)
    print("Sandbox Item forced into ITEM_LOGIN_REQUIRED for update-mode testing.")
    print(f"Item error after reset: {_item_error(item)!r}")
    print("Run biweekly-bills update-link to repair this same Item.")
    return 0


def cmd_sandbox_reauth_test(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment != "sandbox":
        raise RuntimeError("sandbox-reauth-test is disabled outside PLAID_ENV=sandbox.")

    token_before = require_access_token("sandbox")
    client = build_client(settings)
    result = sandbox_reset_login(client, token_before)
    if not result.get("reset_login"):
        raise RuntimeError("Plaid did not confirm Sandbox login reset.")

    broken = get_item(client, token_before)
    print(f"Forced Sandbox Item error: {_item_error(broken)!r}")
    print("Opening Update Mode to repair the same Item...")
    _run_link_flow(settings, update_mode=True)

    token_after = require_access_token("sandbox")
    if token_after != token_before:
        raise RuntimeError("Update Mode unexpectedly changed the persisted access token.")

    repaired = get_item(client, token_after)
    if _item_error(repaired):
        raise RuntimeError(f"Sandbox Item still reports an error after Update Mode: {_item_error(repaired)!r}")

    get_balance(client, token_after)
    print("Sandbox reauthentication test PASSED.")
    print("Same access token retained; Item repaired; Balance access works.")
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    settings = load_settings()
    token = require_access_token(settings.environment)
    client = build_client(settings)
    payload = get_balance(client, token)
    accounts = payload.get("accounts", [])
    selected = load_local_config(settings.environment).get("bills_account_id")
    for account in accounts:
        aid = str(account.get("account_id") or "")
        name = account.get("name") or account.get("official_name") or "(unnamed)"
        mask = account.get("mask") or "----"
        subtype = account.get("subtype") or account.get("type") or ""
        balances = account.get("balances") or {}
        mark = "  <-- Bills Checking" if aid == selected else ""
        print(f"{name} ••••{mask} [{subtype}] current={balances.get('current')} available={balances.get('available')}{mark}")
        print(f"  account_id={aid}")
    return 0


def cmd_use_account(args: argparse.Namespace) -> int:
    settings = load_settings(require_keys=False)
    update_local_config(settings.environment, bills_account_id=args.account_id)
    print(f"{settings.environment.capitalize()} Bills Checking account set to {args.account_id}")
    return 0


def cmd_repair_openformula(args: argparse.Namespace) -> int:
    workbook = choose_workbook(PROJECT_ROOT, args.workbook)
    repair_backup, pre_gui_backup = repair_openformula_namespace(workbook)
    if repair_backup is None:
        print(f"No OpenFormula namespace repair was needed: {workbook}")
        return 0
    print(f"Repaired missing OpenFormula namespace in: {workbook}")
    print(f"Pre-repair backup: {repair_backup}")
    if pre_gui_backup:
        print(f"Verified formula strings against pre-GUI backup: {pre_gui_backup}")
    print("Run biweekly-bills self-test, then reopen the workbook in LibreOffice.")
    return 0


def cmd_install_desktop(args: argparse.Namespace) -> int:
    result = install_desktop_integration()
    action = "Updated" if result.changed else "Already current"
    print(f"{action}: {result.desktop_file}")
    print(f"Application icon: {result.icon_file}")
    print(f"Brand logo: {result.logo_file}")
    print("Wayland desktop ID: biweekly-bills")
    return 0


def cmd_install_gui(args: argparse.Namespace) -> int:
    workbook = choose_workbook(PROJECT_ROOT, args.workbook)
    backup = install_control_panel(workbook)
    print(f"Installed/updated Control Panel in: {workbook}")
    print(f"Pre-GUI backup: {backup}")
    print("Reopen the workbook in LibreOffice and enable macros if prompted.")
    print("Use APPLY / REFRESH WORKBOOK THEME on the Control Panel to style every sheet.")
    return 0


def cmd_self_test(args: argparse.Namespace) -> int:
    workbook = choose_workbook(PROJECT_ROOT, args.workbook)
    print(f"Workbook: {workbook}")
    checks = run_self_test(workbook)
    for check in checks:
        print(f"[PASS] {check.name}: {check.detail}")
    print("Self-test PASSED. Original workbook was not modified.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = load_settings()
    token = require_access_token(settings.environment)
    client = build_client(settings)
    as_of = _parse_date(args.date)
    cycle = args.cycle or cycle_for_day(as_of.day)
    local = load_local_config(settings.environment)
    bills_account_id = args.account or local.get("bills_account_id")
    if not bills_account_id:
        raise RuntimeError(
            "Bills Checking is not selected. Run biweekly-bills accounts, then "
            "biweekly-bills use-bills-account ACCOUNT_ID."
        )

    result = sync_transactions(client, token, cursor=local.get("transactions_cursor"))
    transactions = apply_sync(
        _load_cache(settings.environment),
        added=result.get("added", []),
        modified=result.get("modified", []),
        removed=result.get("removed", []),
    )
    _save_cache(settings.environment, transactions)
    next_cursor = result.get("next_cursor")
    if next_cursor:
        update_local_config(settings.environment, transactions_cursor=next_cursor)
    update_status = str(result.get("transactions_update_status") or "TRANSACTIONS_UPDATE_STATUS_UNKNOWN")

    posted_balance = None
    if not args.no_balance:
        balance_payload = get_balance(client, token)
        posted_balance = _find_account_balance(balance_payload.get("accounts", []), str(bills_account_id))
        if posted_balance is None:
            raise RuntimeError("Bills Checking was not found in Plaid Balance output.")

    workbook = choose_workbook(PROJECT_ROOT, args.workbook)
    matches = inspect_workbook_matches(
        workbook,
        transactions,
        as_of=as_of,
        cycle=cycle,
        bills_account_id=str(bills_account_id),
    )
    changes = plan_workbook_changes(
        workbook,
        transactions,
        as_of=as_of,
        cycle=cycle,
        bills_account_id=str(bills_account_id),
        posted_balance=posted_balance,
        overwrite_paid=args.overwrite_paid,
    )

    print(f"Workbook: {workbook}")
    print(f"Plaid environment: {settings.environment}")
    print(f"Date/cycle: {as_of.isoformat()} / {cycle}")
    print(f"Plaid transaction status: {update_status}")
    print(f"Cached transactions: {len(transactions)}")
    print(f"Matched bills: {len(matches)}")
    for match in matches:
        print(
            f"  {match.bill}: {match.merchant} {match.bank_amount:.2f} on {match.tx_date} "
            f"-> {match.sheet}!{match.cell} (existing={match.existing!r}; {match.status})"
        )
    if update_status == "NOT_READY":
        print("Plaid is still preparing the initial transaction history. Re-run sync after it becomes ready.")
    if not changes:
        print("No workbook changes proposed.")
        return 0
    print("\nProposed changes:")
    for change in changes:
        print(f"  {change.sheet}!{change.cell}: {change.old!r} -> {change.new!r}")
        print(f"    {change.reason}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write these changes.")
        return 0

    if update_status == "NOT_READY":
        raise RuntimeError(
            "Plaid transaction history is still NOT_READY. Refusing --apply so the workbook is not partially reconciled. "
            "Run the dry-run sync again after Plaid finishes the initial pull."
        )

    backup = apply_workbook_changes(workbook, changes)
    print(f"\nApplied {len(changes)} change(s).")
    if backup:
        print(f"Backup: {backup}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biweekly-bills",
        description="Arch-native, free-Trial-safe Navy Federal/Plaid workbook sync.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check local configuration and Trial safety state.")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "production-readiness",
        help="Show the local one-Item Production safety checklist and next safe action.",
    )
    p.set_defaults(func=cmd_production_readiness)

    p = sub.add_parser(
        "clear-production-link-reservation",
        help="Clear a stale initial-Link reservation only after confirming no Production Item was created.",
    )
    p.add_argument(
        "--confirm-no-item-created",
        action="store_true",
        help="Required explicit confirmation that no Production Item was created in the abandoned session.",
    )
    p.set_defaults(func=cmd_clear_production_link_reservation)

    p = sub.add_parser("link", help="Create the one persistent Plaid Item for the active environment.")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("update-link", help="Repair/re-authorize the existing Plaid Item using Link update mode.")
    p.set_defaults(func=cmd_update_link)

    p = sub.add_parser("recover-production", help="Recover a Production Item whose token exchange completed before final persistence.")
    p.set_defaults(func=cmd_recover_production)

    p = sub.add_parser("sandbox-dynamic-item", help="Create a deterministic First Platypus Sandbox Transactions Item.")
    p.set_defaults(func=cmd_sandbox_dynamic_item)

    p = sub.add_parser("sandbox-relink", help="Archive the current Sandbox Item locally and relaunch Sandbox Link.")
    p.set_defaults(func=cmd_sandbox_relink)

    p = sub.add_parser("sandbox-seed-bills", help="Create known fake bill transactions for Sandbox matching tests.")
    p.add_argument("--date", help="ISO date for the fake posted transactions; defaults to today.")
    p.set_defaults(func=cmd_sandbox_seed_bills)

    p = sub.add_parser("sandbox-reset-login", help="Force the Sandbox Item into ITEM_LOGIN_REQUIRED.")
    p.set_defaults(func=cmd_sandbox_reset_login)

    p = sub.add_parser("sandbox-reauth-test", help="Force ITEM_LOGIN_REQUIRED, repair via Update Mode, and verify the same token.")
    p.set_defaults(func=cmd_sandbox_reauth_test)

    p = sub.add_parser("accounts", help="List accounts and current Balance data.")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("use-bills-account", help="Choose the Bills Checking account for the active environment.")
    p.add_argument("account_id")
    p.set_defaults(func=cmd_use_account)

    p = sub.add_parser("repair-openformula", help="Repair a missing OpenFormula namespace without changing formula strings.")
    p.add_argument("--workbook")
    p.set_defaults(func=cmd_repair_openformula)

    p = sub.add_parser(
        "install-desktop",
        help="Install/update the Linux desktop launcher, Wayland app icon, and branding assets.",
    )
    p.set_defaults(func=cmd_install_desktop)

    p = sub.add_parser("install-gui", help="Install/update the LibreOffice Control Panel and workbook theme controls.")
    p.add_argument("--workbook")
    p.set_defaults(func=cmd_install_gui)

    p = sub.add_parser("self-test", help="Test ODS matching, write, backup, and preservation on a temporary copy.")
    p.add_argument("--workbook")
    p.set_defaults(func=cmd_self_test)

    p = sub.add_parser("sync", help="Match posted transactions and update the workbook.")
    p.add_argument("--account")
    p.add_argument("--workbook")
    p.add_argument("--date", help="ISO date; defaults to today.")
    p.add_argument("--cycle", choices=["1st", "15th"])
    p.add_argument("--no-balance", action="store_true", help="Skip Balance reconciliation.")
    p.add_argument("--overwrite-paid", action="store_true", help="Allow replacing existing Paid values.")
    p.add_argument("--apply", action="store_true", help="Write changes after previewing them.")
    p.set_defaults(func=cmd_sync)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args) or 0)
    except (RuntimeError, FileNotFoundError, ValueError, plaid.ApiException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
