"""Stable per-user locations for everything the app must keep across updates.

Older releases kept some connection state next to the installed code (the
"project root"): the Production Item lock, the per-environment local config
(transaction cursor and Bills Checking choice), and transaction caches. For a
wheel install that folder is inside the virtual environment, so reinstalling,
recreating the venv after a Python upgrade, or moving from a Git clone to a
wheel silently dropped that state.

Everything now lives under the user's XDG config/data folders, which no
install, upgrade or uninstall touches. ``migrate_legacy_state`` copies any
old project-root files into place the first time a new version starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil


APP_FOLDER = "bi-weekly-bills"

# Location of the installed package's parent folder. For a Git clone this is
# the repository; for a wheel it is inside the virtual environment.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def user_config_dir() -> Path:
    # Deliberately ~/.config (not $XDG_CONFIG_HOME): the saved Plaid access
    # token has always been stored here, and moving it would look like a lost
    # bank connection.
    return Path.home() / ".config" / APP_FOLDER


def user_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_FOLDER


def user_state_dir() -> Path:
    """Non-secret connection state (cursor, account choice, item lock)."""
    return user_config_dir() / "state"


# Files an older install may have written under its project root, mapped to
# where they belong now. Paths are relative to the legacy project root.
_LEGACY_STATE_FILES: tuple[tuple[str, str], ...] = (
    ("PRODUCTION_ITEM_CREATED.lock", "PRODUCTION_ITEM_CREATED.lock"),
    ("data/local_config_production.json", "local_config_production.json"),
    ("data/local_config_sandbox.json", "local_config_sandbox.json"),
    ("data/local_config.json", "local_config.json"),
    ("data/transactions_production.json", "transactions_production.json"),
    ("data/transactions_sandbox.json", "transactions_sandbox.json"),
    ("data/transactions.json", "transactions.json"),
)

_MIGRATION_MARKER = ".legacy-state-migrated"


@dataclass
class MigrationReport:
    source: Path
    copied: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    imported_api_keys: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.copied or self.imported_api_keys)


def _copy_private(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temp = target.with_name(f".{target.name}.migrating")
    shutil.copy2(source, temp)
    os.chmod(temp, 0o600)
    os.replace(temp, target)


def migrate_legacy_state(
    legacy_root: Path | None = None,
    *,
    force: bool = False,
) -> MigrationReport:
    """Copy connection state from an old install folder into the user folders.

    Nothing is deleted from the old location, and files that already exist in
    the new location are never overwritten, so this is safe to run repeatedly.
    The Plaid access token itself has always lived in ~/.config and is not
    touched here.
    """
    root = Path(legacy_root) if legacy_root is not None else PROJECT_ROOT
    report = MigrationReport(source=root)
    state_dir = user_state_dir()
    marker = state_dir / _MIGRATION_MARKER

    automatic = legacy_root is None
    if automatic and marker.exists() and not force:
        return report

    for relative, target_name in _LEGACY_STATE_FILES:
        source = root / relative
        if not source.is_file():
            continue
        target = state_dir / target_name
        if target.exists():
            report.skipped_existing.append(relative)
            continue
        _copy_private(source, target)
        report.copied.append(relative)

    # A Git-clone install may have kept the Plaid Client ID/Secret in the
    # repository's .env. Bring them along only if the user has none saved yet.
    from . import settings as settings_module

    legacy_env = root / ".env"
    user_env = settings_module.USER_ENV_PATH
    if legacy_env.is_file() and not user_env.exists():
        from dotenv import dotenv_values

        values = dotenv_values(legacy_env)
        if (values.get("PLAID_CLIENT_ID") or "").strip() and (
            values.get("PLAID_SECRET") or ""
        ).strip():
            _copy_private(legacy_env, user_env)
            report.imported_api_keys = True

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        if automatic:
            marker.write_text(f"source={root}\n", encoding="utf-8")
    except OSError:
        pass
    return report
