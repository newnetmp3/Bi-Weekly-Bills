from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import tempfile

from .settings import PROJECT_ROOT

PRODUCTION_LOCK = PROJECT_ROOT / "PRODUCTION_ITEM_CREATED.lock"
PRODUCTION_LINK_SESSION_LOCK = PROJECT_ROOT / "PRODUCTION_LINK_IN_PROGRESS.lock"

_ITEM_LINE = re.compile(r"^Item ID:\s*(?P<item>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ProductionLockInfo:
    path: Path
    item_id: str | None
    raw_text: str
    valid: bool


def load_production_lock() -> ProductionLockInfo | None:
    if not PRODUCTION_LOCK.exists():
        return None

    raw = PRODUCTION_LOCK.read_text(encoding="utf-8", errors="replace")
    match = _ITEM_LINE.search(raw)
    item_id = str(match.group("item")).strip() if match else None
    return ProductionLockInfo(
        path=PRODUCTION_LOCK,
        item_id=item_id or None,
        raw_text=raw,
        valid=bool(item_id),
    )


def production_lock_item_id() -> str | None:
    info = load_production_lock()
    return info.item_id if info and info.valid else None


def mark_production_item_created(item_id: str) -> Path:
    item_id = item_id.strip()
    if not item_id:
        raise ValueError("Production item_id is required.")

    existing = load_production_lock()
    if existing is not None:
        if not existing.valid:
            raise RuntimeError(
                "Production Item lock exists but its Item ID cannot be parsed. "
                "Refusing to overwrite it automatically."
            )
        if existing.item_id != item_id:
            raise RuntimeError(
                "Refusing to replace the existing Production Item lock with a "
                "different Item ID. The one persistent Production Item policy "
                "requires recovery or manual investigation instead."
            )
        return PRODUCTION_LOCK

    payload = (
        "A Production Plaid Item already exists for this project.\n"
        "Do not create another Item for ordinary reauthentication; use update mode.\n"
        f"Item ID: {item_id}\n"
        f"Recorded at: {datetime.now().astimezone().isoformat()}\n"
    )

    PRODUCTION_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".production-item-lock.",
        suffix=".tmp",
        dir=PRODUCTION_LOCK.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, PRODUCTION_LOCK)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return PRODUCTION_LOCK


def production_item_locked() -> bool:
    return PRODUCTION_LOCK.exists()


def acquire_initial_link_session() -> Path:
    """Reserve the one allowed Production initial-link session.

    This prevents two local initial-link processes from being opened at once.
    The reservation contains no secret and is intentionally conservative: if a
    previous session crashed, the user must clear it explicitly after confirming
    that no Plaid Item was created.
    """

    if PRODUCTION_LINK_SESSION_LOCK.exists():
        raise RuntimeError(
            "A Production initial-link session is already reserved. Do not start "
            "another Link session until the existing session is completed or the "
            "reservation is deliberately cleared after verifying no Item was created."
        )

    payload = (
        "Production initial Link is in progress.\n"
        "Do not start a second Production Link session.\n"
        f"Started at: {datetime.now().astimezone().isoformat()}\n"
    )

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(PRODUCTION_LINK_SESSION_LOCK, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            PRODUCTION_LINK_SESSION_LOCK.unlink()
        except FileNotFoundError:
            pass
        raise
    return PRODUCTION_LINK_SESSION_LOCK


def release_initial_link_session() -> None:
    try:
        PRODUCTION_LINK_SESSION_LOCK.unlink()
    except FileNotFoundError:
        pass


def initial_link_session_reserved() -> bool:
    return PRODUCTION_LINK_SESSION_LOCK.exists()


def clear_initial_link_session_after_confirmation() -> None:
    """Clear a stale initial-Link reservation only when no Item evidence exists."""
    from .secure_store import (
        load_credentials,
        load_pending_production_exchange,
    )

    production = load_credentials("production")
    pending = load_pending_production_exchange()
    if production.get("access_token") or production.get("item_id"):
        raise RuntimeError(
            "Cannot clear the Production Link reservation because a Production "
            "credential exists. Initial Link must remain disabled."
        )
    if pending.get("access_token") or pending.get("item_id"):
        raise RuntimeError(
            "Cannot clear the Production Link reservation because a pending "
            "Production exchange exists. Recover that Item instead."
        )
    if production_item_locked():
        raise RuntimeError(
            "Cannot clear the Production Link reservation because the persistent "
            "Production Item lock exists. Recover the existing Item instead."
        )
    release_initial_link_session()
