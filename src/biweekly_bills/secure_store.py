from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any

APP_DIR = Path.home() / ".config" / "bi-weekly-bills"
LEGACY_CREDENTIALS_PATH = APP_DIR / "plaid_item.json"
PENDING_PRODUCTION_PATH = APP_DIR / "plaid_item_production.pending.json"


def credentials_path(environment: str) -> Path:
    env = environment.strip().lower()
    if env not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")
    return APP_DIR / f"plaid_item_{env}.json"


def preflight_store() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(APP_DIR, 0o700)
    fd, name = tempfile.mkstemp(prefix=".write-test-", dir=APP_DIR)
    os.close(fd)
    os.unlink(name)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    preflight_store()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=APP_DIR,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def migrate_legacy_credentials(environment: str) -> Path | None:
    target = credentials_path(environment)
    if target.exists() or not LEGACY_CREDENTIALS_PATH.exists():
        return None

    legacy = _load_json(LEGACY_CREDENTIALS_PATH)
    stored_env = str(legacy.get("environment") or "").strip().lower()
    if stored_env != environment:
        return None

    os.replace(LEGACY_CREDENTIALS_PATH, target)
    os.chmod(target, 0o600)
    return target


def load_credentials(environment: str) -> dict[str, Any]:
    migrate_legacy_credentials(environment)
    return _load_json(credentials_path(environment))


def save_credentials(*, access_token: str, item_id: str, environment: str) -> None:
    environment = environment.strip().lower()
    if environment not in {"sandbox", "production"}:
        raise ValueError(f"Unsupported Plaid environment: {environment}")

    if environment == "production":
        from .production_guard import load_production_lock

        lock = load_production_lock()
        if lock is not None:
            if not lock.valid:
                raise RuntimeError(
                    "Production Item lock exists but its Item ID cannot be parsed. "
                    "Refusing to write Production credentials."
                )
            if lock.item_id != item_id:
                raise RuntimeError(
                    "Refusing to write Production credentials for an Item ID that "
                    "does not match the persistent Production Item lock."
                )

        target = credentials_path("production")
        existing = _load_json(target)
        if existing:
            existing_item = str(existing.get("item_id") or "").strip()
            if not existing_item:
                raise RuntimeError(
                    "Production credential file exists but has no Item ID. "
                    "Refusing to overwrite ambiguous Production state."
                )
            if existing_item != item_id:
                raise RuntimeError(
                    "Refusing to overwrite the persisted Production credential "
                    "with a different Item ID. Recover or repair the existing Item instead."
                )

    payload = {
        "access_token": access_token,
        "item_id": item_id,
        "environment": environment,
        "saved_at": datetime.now().astimezone().isoformat(),
    }
    _atomic_write_json(credentials_path(environment), payload)


def require_access_token(environment: str) -> str:
    data = load_credentials(environment)
    token = str(data.get("access_token") or "")
    if not token:
        raise RuntimeError(
            f"No persisted Plaid {environment} Item credential exists. "
            "Run biweekly-bills link first."
        )
    return token


def archive_sandbox_credentials() -> Path | None:
    path = credentials_path("sandbox")
    migrate_legacy_credentials("sandbox")
    if not path.exists():
        return None

    data = _load_json(path)
    if str(data.get("environment") or "") != "sandbox":
        raise RuntimeError("Refusing to archive a non-Sandbox Plaid credential.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = APP_DIR / f"plaid_item_sandbox.{stamp}.bak.json"
    os.replace(path, backup)
    os.chmod(backup, 0o600)
    return backup


def save_pending_production_exchange(*, access_token: str, item_id: str) -> Path:
    item_id = item_id.strip()
    if not access_token or not item_id:
        raise ValueError("A complete Production access token and Item ID are required.")

    from .production_guard import load_production_lock

    lock = load_production_lock()
    if lock is not None:
        if not lock.valid:
            raise RuntimeError(
                "Production Item lock exists but its Item ID cannot be parsed. "
                "Refusing to write pending Production recovery state."
            )
        if lock.item_id != item_id:
            raise RuntimeError(
                "Pending Production Item does not match the persistent Production Item lock."
            )

    existing_pending = _load_json(PENDING_PRODUCTION_PATH)
    if existing_pending:
        pending_item = str(existing_pending.get("item_id") or "").strip()
        if not pending_item:
            raise RuntimeError(
                "A pending Production recovery file exists without an Item ID. "
                "Refusing to overwrite ambiguous recovery state."
            )
        if pending_item != item_id:
            raise RuntimeError(
                "Refusing to replace a pending Production exchange with a different Item ID."
            )

    existing_credentials = _load_json(credentials_path("production"))
    if existing_credentials:
        existing_item = str(existing_credentials.get("item_id") or "").strip()
        if not existing_item:
            raise RuntimeError(
                "Production credential file exists without an Item ID. "
                "Refusing to create new pending Production state."
            )
        if existing_item != item_id:
            raise RuntimeError(
                "Pending Production Item does not match the persisted Production Item."
            )

    payload = {
        "access_token": access_token,
        "item_id": item_id,
        "environment": "production",
        "state": "exchange_completed_pending_final_save",
        "created_at": datetime.now().astimezone().isoformat(),
    }
    _atomic_write_json(PENDING_PRODUCTION_PATH, payload)
    return PENDING_PRODUCTION_PATH


def load_pending_production_exchange() -> dict[str, Any]:
    return _load_json(PENDING_PRODUCTION_PATH)


def clear_pending_production_exchange() -> None:
    try:
        PENDING_PRODUCTION_PATH.unlink()
    except FileNotFoundError:
        pass


def recover_pending_production_credentials() -> dict[str, Any]:
    pending = load_pending_production_exchange()
    access_token = str(pending.get("access_token") or "")
    item_id = str(pending.get("item_id") or "")
    if not access_token or not item_id:
        raise RuntimeError("No complete pending Production credential exists to recover.")

    existing = load_credentials("production")
    existing_item = str(existing.get("item_id") or "").strip()
    if existing:
        if not existing_item:
            raise RuntimeError(
                "Production credential file exists without an Item ID. "
                "Refusing recovery until the ambiguity is resolved."
            )
        if existing_item != item_id:
            raise RuntimeError(
                "Pending Production recovery Item does not match the existing "
                "Production credential Item. Refusing to overwrite either Item."
            )

    save_credentials(
        access_token=access_token,
        item_id=item_id,
        environment="production",
    )
    clear_pending_production_exchange()
    return {"item_id": item_id, "environment": "production"}
