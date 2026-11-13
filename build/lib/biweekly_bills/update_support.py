"""Helpers that let an app update keep the existing bank connection.

None of these functions contact Plaid or open Link. They only read the local
files that prove which bank connection (Plaid Item) this install already has,
so an update can be verified without signing in to the bank again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .database import Database, default_database_path
from .paths import user_config_dir, user_data_dir, user_state_dir
from .production_readiness import inspect_production_readiness
from .secure_store import credentials_path, load_credentials
from .settings import load_settings


@dataclass(frozen=True)
class ConnectionSnapshot:
    version: str
    environment: str
    api_keys_configured: bool
    credential_present: bool
    item_id: str | None
    readiness_action: str
    readiness_headline: str
    database_path: Path
    database_present: bool

    @property
    def connection_ready(self) -> bool:
        """True when Sync can run with no bank sign-in."""
        return (
            self.environment == "production"
            and self.api_keys_configured
            and self.credential_present
            and self.readiness_action == "UPDATE_MODE_ONLY"
        )


def connection_snapshot(database_path: Path | None = None) -> ConnectionSnapshot:
    settings = load_settings(require_keys=False)
    production = load_credentials("production")
    db_path = Path(database_path) if database_path else default_database_path()
    database = Database(db_path) if db_path.exists() else None
    readiness = inspect_production_readiness(database)
    item_id = str(production.get("item_id") or "").strip() or None
    return ConnectionSnapshot(
        version=__version__,
        environment=settings.environment,
        api_keys_configured=bool(settings.client_id and settings.secret),
        credential_present=bool(
            str(production.get("access_token") or "").strip() and item_id
        ),
        item_id=item_id,
        readiness_action=readiness.action,
        readiness_headline=readiness.headline,
        database_path=db_path,
        database_present=db_path.exists(),
    )


def mask_item_id(item_id: str | None) -> str:
    if not item_id:
        return "none"
    return item_id if len(item_id) <= 8 else f"…{item_id[-6:]}"


@dataclass(frozen=True)
class UpdateVerification:
    ok: bool
    lines: tuple[str, ...]


def verify_update(
    expected_item_id: str | None = None,
    *,
    database_path: Path | None = None,
) -> UpdateVerification:
    snap = connection_snapshot(database_path)
    lines: list[str] = [f"Bi-Weekly Bills {snap.version}"]
    ok = True

    def check(passed: bool, good: str, bad: str) -> None:
        nonlocal ok
        lines.append(f"  [{'OK' if passed else '!!'}] {good if passed else bad}")
        ok = ok and passed

    check(
        snap.database_present,
        f"Bills database found: {snap.database_path}",
        f"Bills database not found at {snap.database_path}",
    )
    check(
        snap.api_keys_configured,
        "Plaid API keys are saved",
        "Plaid API keys are missing: open Settings and re-enter the Client ID "
        "and Secret (this does NOT require a bank sign-in)",
    )
    check(
        snap.credential_present,
        f"Saved bank connection found ({credentials_path('production')})",
        "No saved bank connection was found in ~/.config/bi-weekly-bills",
    )
    if expected_item_id:
        check(
            snap.item_id == expected_item_id,
            f"Same bank connection as before the update (Item {mask_item_id(snap.item_id)})",
            "The saved bank connection does not match the one recorded before "
            f"the update (expected {mask_item_id(expected_item_id)}, found "
            f"{mask_item_id(snap.item_id)})",
        )
    check(
        snap.readiness_action == "UPDATE_MODE_ONLY",
        "Safety checks pass: Sync will reuse the existing connection",
        f"Safety checks report {snap.readiness_action}: {snap.readiness_headline}",
    )

    if ok:
        lines.append("")
        lines.append(
            "Your bank connection was carried over. No bank sign-in is needed; "
            "use Sync in the app as usual."
        )
    return UpdateVerification(ok=ok, lines=tuple(lines))


def storage_locations() -> dict[str, Path]:
    return {
        "Bills database": default_database_path(),
        "Backups": default_database_path().parent / "backups",
        "Bank connection & API keys": user_config_dir(),
        "Sync state": user_state_dir(),
        "Update snapshots": user_data_dir() / "update-snapshots",
    }
