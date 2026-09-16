from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat
from typing import Any

from .database import Database
from .production_guard import (
    initial_link_session_reserved,
    load_production_lock,
)
from .sandbox_validation import load_sandbox_validation_marker
from .secure_store import (
    PENDING_PRODUCTION_PATH,
    credentials_path,
    load_credentials,
    load_pending_production_exchange,
)
from .settings import load_settings


@dataclass(frozen=True)
class ProductionReadinessCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ProductionReadinessReport:
    action: str
    headline: str
    checks: tuple[ProductionReadinessCheck, ...]
    production_item_id: str | None
    can_initial_link: bool
    can_update_mode: bool
    can_recover: bool

    @property
    def blocked(self) -> bool:
        return self.action == "BLOCKED"


def _complete_credential(data: dict[str, Any]) -> tuple[bool, str | None]:
    token = str(data.get("access_token") or "").strip()
    item_id = str(data.get("item_id") or "").strip()
    return bool(token and item_id), item_id or None


def _file_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(stat.S_IMODE(path.stat().st_mode))


def inspect_production_readiness(
    database: Database | None = None,
) -> ProductionReadinessReport:
    settings = load_settings(require_keys=False)
    sandbox_validation = load_sandbox_validation_marker()
    sandbox = load_credentials("sandbox")
    production = load_credentials("production")
    pending = load_pending_production_exchange()
    lock = load_production_lock()

    checks: list[ProductionReadinessCheck] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(
            ProductionReadinessCheck(
                name=name,
                status=status,
                detail=detail,
            )
        )

    validation_passed = bool(sandbox_validation.get("passed"))
    if validation_passed:
        finished = str(sandbox_validation.get("finished_at") or "recorded previously")
        add(
            "Sandbox validation",
            "PASS",
            f"Full isolated Sandbox validation is recorded as passed ({finished}).",
        )
    else:
        add(
            "Sandbox validation",
            "WARN",
            "No recorded isolated Sandbox validation pass exists. "
            "This is recommended for development/QA but is not required "
            "for a guarded first bank connection.",
        )

    if settings.environment == "production":
        if settings.client_id and settings.secret:
            add(
                "Production environment armed",
                "PASS",
                "PLAID_ENV=production and API credentials are configured.",
            )
        else:
            add(
                "Production environment armed",
                "FAIL",
                "PLAID_ENV=production but the Plaid Client ID/Secret is incomplete.",
            )
    else:
        add(
            "Production environment armed",
            "PASS",
            "PLAID_ENV is still sandbox. Production cannot be contacted accidentally from the current app process.",
        )

    sandbox_complete, sandbox_item = _complete_credential(sandbox)
    prod_complete, prod_item = _complete_credential(production)
    pending_complete, pending_item = _complete_credential(pending)

    prod_path = credentials_path("production")
    prod_file_exists = prod_path.exists()
    if production and not prod_complete:
        add(
            "Production credential",
            "FAIL",
            "Production credential file exists but is incomplete. Do not run Initial Link.",
        )
    elif prod_complete:
        add(
            "Production credential",
            "PASS",
            f"Existing persistent Production Item is recorded: {prod_item}. Initial Link is permanently disabled.",
        )
    else:
        add(
            "Production credential",
            "PASS",
            "No persistent Production credential exists yet.",
        )

    if pending and not pending_complete:
        add(
            "Pending Production recovery",
            "FAIL",
            "Pending Production recovery state exists but is incomplete. Do not create another Item.",
        )
    elif pending_complete:
        add(
            "Pending Production recovery",
            "WARN",
            f"An exchanged Production Item is waiting for recovery: {pending_item}. Recovery is the only safe next action.",
        )
    else:
        add(
            "Pending Production recovery",
            "PASS",
            "No pending Production token exchange needs recovery.",
        )

    lock_item = lock.item_id if lock and lock.valid else None
    if lock is not None and not lock.valid:
        add(
            "Persistent Production Item lock",
            "FAIL",
            "Production Item lock exists but its Item ID cannot be parsed. Refusing automatic replacement.",
        )
    elif lock_item:
        add(
            "Persistent Production Item lock",
            "PASS",
            f"Persistent one-Item lock records Item {lock_item}.",
        )
    else:
        add(
            "Persistent Production Item lock",
            "PASS",
            "No Production Item lock exists yet.",
        )

    session_reserved = initial_link_session_reserved()
    if session_reserved:
        add(
            "Initial-Link session reservation",
            "WARN",
            "A Production initial-Link session is reserved. A second Initial Link is blocked.",
        )
    else:
        add(
            "Initial-Link session reservation",
            "PASS",
            "No Production initial-Link session is currently reserved.",
        )

    identifiers = {
        value
        for value in (prod_item, pending_item, lock_item)
        if value
    }
    conflict = len(identifiers) > 1
    if conflict:
        add(
            "Production Item identity consistency",
            "FAIL",
            "Production credential, pending recovery, and lock files reference different Item IDs. Manual investigation is required; do not Link.",
        )
    else:
        common_id = next(iter(identifiers), None)
        add(
            "Production Item identity consistency",
            "PASS",
            (
                f"All persisted Production state agrees on Item {common_id}."
                if common_id
                else "No Production Item identity has been created yet."
            ),
        )

    if sandbox_item and sandbox_item in identifiers:
        add(
            "Sandbox / Production separation",
            "FAIL",
            "The same Item ID appears in Sandbox and Production state. Refusing Production actions until the environment mix-up is resolved.",
        )
        environment_collision = True
    else:
        add(
            "Sandbox / Production separation",
            "PASS",
            "Sandbox and Production credential stores are separate and do not share an Item ID.",
        )
        environment_collision = False

    permission_failure = False
    for label, path in (
        ("Production credential", prod_path),
        ("Pending Production recovery", PENDING_PRODUCTION_PATH),
    ):
        mode = _file_mode(path)
        if mode is None:
            continue
        if mode != "0o600":
            permission_failure = True
            add(
                f"{label} permissions",
                "FAIL",
                f"{path} has mode {mode}; expected 0o600.",
            )
        else:
            add(
                f"{label} permissions",
                "PASS",
                f"{path.name} is mode 0o600.",
            )

    if database is not None:
        production_accounts = database.list_bank_accounts("production")
        production_transactions = database.list_bank_transactions(
            "production",
            limit=1,
        )
        if (production_accounts or production_transactions) and not prod_complete:
            add(
                "Production SQLite cache",
                "WARN",
                "Production bank cache exists without a complete persistent Production credential. Treat it as stale evidence of an existing Item and do not Initial Link until investigated.",
            )
            stale_cache = True
        else:
            add(
                "Production SQLite cache",
                "PASS",
                (
                    f"{len(production_accounts)} Production account row(s) are consistent with the existing Item."
                    if prod_complete
                    else "No Production bank cache exists before first Link."
                ),
            )
            stale_cache = False
    else:
        stale_cache = False

    structural_failure = any(
        check.status == "FAIL"
        for check in checks
        if check.name != "Sandbox validation"
    )

    if conflict or environment_collision or permission_failure or stale_cache:
        structural_failure = True

    if pending_complete and not structural_failure:
        action = "RECOVER"
        headline = "Recover the existing Production Item; do not create another Item."
        can_initial_link = False
        can_update = False
        can_recover = True
        production_item_id = pending_item
    elif prod_complete and not structural_failure:
        action = "UPDATE_MODE_ONLY"
        headline = "Production Item already exists. Initial Link is permanently disabled; use Update Mode for repair."
        can_initial_link = False
        can_update = True
        can_recover = False
        production_item_id = prod_item
    elif lock_item and not prod_complete and not pending_complete:
        action = "BLOCKED"
        headline = "A Production Item lock proves an Item existed, but its credential is missing. Recover it; never relink."
        can_initial_link = False
        can_update = False
        can_recover = False
        production_item_id = lock_item
    elif session_reserved:
        action = "BLOCKED"
        headline = "A Production initial-Link session is already reserved. Do not open another."
        can_initial_link = False
        can_update = False
        can_recover = False
        production_item_id = None
    elif structural_failure:
        action = "BLOCKED"
        headline = "Production state is inconsistent. Initial Link is blocked until the failed checks are resolved."
        can_initial_link = False
        can_update = False
        can_recover = False
        production_item_id = next(iter(identifiers), None)
    elif settings.environment != "production":
        action = "READY_TO_ARM"
        headline = "Production state is clean. The next deliberate step is to switch to Production credentials and run readiness again."
        can_initial_link = False
        can_update = False
        can_recover = False
        production_item_id = None
    elif not settings.client_id or not settings.secret:
        action = "BLOCKED"
        headline = "Production environment is selected but API credentials are incomplete."
        can_initial_link = False
        can_update = False
        can_recover = False
        production_item_id = None
    else:
        action = "READY_FOR_FIRST_LINK"
        headline = "All local guardrails are satisfied for the one allowed Production Initial Link."
        can_initial_link = True
        can_update = False
        can_recover = False
        production_item_id = None

    return ProductionReadinessReport(
        action=action,
        headline=headline,
        checks=tuple(checks),
        production_item_id=production_item_id,
        can_initial_link=can_initial_link,
        can_update_mode=can_update,
        can_recover=can_recover,
    )


def assert_initial_production_link_allowed(
    database: Database | None = None,
    *,
    allow_reserved_session: bool = False,
) -> ProductionReadinessReport:
    report = inspect_production_readiness(database)

    if allow_reserved_session and report.action == "BLOCKED":
        disallowed_checks = [
            check
            for check in report.checks
            if check.status == "FAIL"
            or (
                check.status == "WARN"
                and check.name not in {
                    "Initial-Link session reservation",
                    "Sandbox validation",
                }
            )
        ]
        only_session_block = (
            initial_link_session_reserved()
            and not disallowed_checks
            and not load_credentials("production")
            and not load_pending_production_exchange()
            and load_production_lock() is None
        )
        settings = load_settings(require_keys=False)
        if (
            only_session_block
            and settings.environment == "production"
            and settings.client_id
            and settings.secret
        ):
            return report

    if not report.can_initial_link:
        raise RuntimeError(report.headline)
    return report


def assert_production_update_allowed(
    database: Database | None = None,
) -> ProductionReadinessReport:
    report = inspect_production_readiness(database)
    settings = load_settings(require_keys=False)
    if settings.environment != "production":
        raise RuntimeError(
            "Update Mode for the Production Item requires PLAID_ENV=production."
        )
    if not report.can_update_mode:
        raise RuntimeError(report.headline)
    return report


def assert_production_recovery_allowed(
    database: Database | None = None,
) -> ProductionReadinessReport:
    report = inspect_production_readiness(database)
    if not report.can_recover:
        raise RuntimeError(report.headline)
    return report


def assert_production_sync_allowed(
    database: Database | None = None,
) -> ProductionReadinessReport:
    """Require a healthy existing Production Item for read-only bank sync."""
    report = inspect_production_readiness(database)
    settings = load_settings(require_keys=False)
    if settings.environment != "production":
        raise RuntimeError(
            "Production bank sync requires PLAID_ENV=production."
        )
    if report.action != "UPDATE_MODE_ONLY":
        raise RuntimeError(
            "Production bank sync requires the existing persistent Item to be "
            f"healthy and locked. Current readiness: {report.action}. {report.headline}"
        )
    if not report.production_item_id:
        raise RuntimeError(
            "Production readiness did not resolve a persistent Item ID."
        )
    return report
