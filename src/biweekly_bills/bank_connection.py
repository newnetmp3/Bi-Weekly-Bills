from __future__ import annotations

from dataclasses import dataclass
import threading
import time
import webbrowser

from werkzeug.serving import make_server

from .bank_sync import BankSyncReport, sync_production_to_sqlite
from .database import Database
from .link_server import create_app
from .plaid_sdk import build_client, get_item
from .production_guard import (
    acquire_initial_link_session,
    mark_production_item_created,
    release_initial_link_session,
)
from .production_readiness import (
    inspect_production_readiness,
    assert_initial_production_link_allowed,
    assert_production_recovery_allowed,
    assert_production_update_allowed,
)
from .secure_store import (
    load_credentials,
    load_pending_production_exchange,
    preflight_store,
    recover_pending_production_credentials,
    require_access_token,
)
from .settings import load_settings


@dataclass(frozen=True)
class ConnectionState:
    action: str
    headline: str
    detail: str
    primary_label: str
    can_repair: bool


@dataclass(frozen=True)
class ConnectionResult:
    action: str
    message: str
    sync_report: BankSyncReport | None = None


def connection_state(database: Database) -> ConnectionState:
    settings = load_settings(require_keys=False)
    production = load_credentials("production")
    pending = load_pending_production_exchange()
    keys_ready = bool(settings.client_id and settings.secret)
    bank_mode_ready = settings.environment == "production"

    if pending.get("access_token") and pending.get("item_id"):
        if not keys_ready or not bank_mode_ready:
            return ConnectionState(
                action="configure",
                headline="Connection recovery is waiting",
                detail=(
                    "An existing bank connection needs recovery. Save the Plaid "
                    "API settings below first; the app will recover this same "
                    "connection and will not create another one."
                ),
                primary_label="Save bank API settings",
                can_repair=False,
            )
        return ConnectionState(
            action="recover",
            headline="Connection recovery is ready",
            detail=(
                "A bank connection was already created but its final local save "
                "needs to be completed. Recover it instead of creating another link."
            ),
            primary_label="Recover connection",
            can_repair=False,
        )

    if production.get("access_token") and production.get("item_id"):
        if not keys_ready or not bank_mode_ready:
            return ConnectionState(
                action="configure",
                headline="Bank connection exists",
                detail=(
                    "The saved connection is intact. Enter/save the Plaid API "
                    "settings below so the app can use it; no relink is needed."
                ),
                primary_label="Save bank API settings",
                can_repair=False,
            )

        report = inspect_production_readiness(database)
        if report.action != "UPDATE_MODE_ONLY":
            return ConnectionState(
                action="blocked",
                headline="Bank connection needs attention",
                detail=report.headline,
                primary_label="Refresh setup status",
                can_repair=False,
            )

        return ConnectionState(
            action="sync",
            headline="Bank is connected",
            detail=(
                "Balances and transactions can be refreshed without creating "
                "another bank connection."
            ),
            primary_label="Sync now",
            can_repair=True,
        )

    if not keys_ready or not bank_mode_ready:
        return ConnectionState(
            action="configure",
            headline="Bank setup needs API credentials",
            detail=(
                "Enter the Client ID and Secret from your Plaid dashboard once, "
                "then connect your bank in the browser."
            ),
            primary_label="Save bank API settings",
            can_repair=False,
        )

    report = inspect_production_readiness(database)
    if report.can_initial_link:
        return ConnectionState(
            action="connect",
            headline="Ready to connect your bank",
            detail=(
                "One browser window will open. Sign in through Plaid and return "
                "to the app when the browser confirms the connection."
            ),
            primary_label="Connect bank",
            can_repair=False,
        )

    return ConnectionState(
        action="blocked",
        headline="Bank setup needs attention",
        detail=report.headline,
        primary_label="Refresh setup status",
        can_repair=False,
    )


def _run_link_browser(database: Database, *, update_mode: bool) -> None:
    settings = load_settings()
    access_token = require_access_token("production") if update_mode else None

    if update_mode:
        assert_production_update_allowed(database)
    else:
        assert_initial_production_link_allowed(database)
        acquire_initial_link_session()

    completion = threading.Event()
    try:
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
            time.sleep(0.5)
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()
        try:
            while not completion.is_set():
                server.handle_request()
        finally:
            server.server_close()
    finally:
        if not update_mode:
            release_initial_link_session()

    if update_mode:
        token = require_access_token("production")
        item = get_item(build_client(settings), token).get("item") or {}
        if item.get("error"):
            raise RuntimeError(
                "Bank repair was not completed. "
                f"The connection still reports: {item.get('error')}"
            )
        return

    saved = load_credentials("production")
    if not saved.get("access_token") or not saved.get("item_id"):
        pending = load_pending_production_exchange()
        if pending.get("access_token") and pending.get("item_id"):
            raise RuntimeError(
                "The bank connection was created but still needs recovery. "
                "Use Recover connection in Settings; do not connect again."
            )
        raise RuntimeError(
            "Bank connection was closed before setup completed. "
            "No saved connection was found."
        )


def run_connection_action(
    database: Database,
    action: str,
) -> ConnectionResult:
    normalized = action.strip().casefold()
    preflight_store()

    if normalized == "connect":
        _run_link_browser(database, update_mode=False)
        sync = sync_production_to_sqlite(database)
        return ConnectionResult(
            action="connect",
            message=(
                f"Bank connected and synced: {sync.account_count} account(s), "
                f"{sync.transaction_count} stored transaction(s)."
            ),
            sync_report=sync,
        )

    if normalized == "repair":
        settings = load_settings()
        credential = load_credentials("production")
        item_id = str(credential.get("item_id") or "")
        if not item_id:
            raise RuntimeError("The saved bank connection is missing its Item ID.")
        mark_production_item_created(item_id)
        _run_link_browser(database, update_mode=True)
        sync = sync_production_to_sqlite(database)
        return ConnectionResult(
            action="repair",
            message=(
                f"Bank connection repaired and synced: "
                f"{sync.account_count} account(s)."
            ),
            sync_report=sync,
        )

    if normalized == "recover":
        assert_production_recovery_allowed(database)
        pending = load_pending_production_exchange()
        item_id = str(pending.get("item_id") or "")
        if not item_id:
            raise RuntimeError(
                "No incomplete bank connection is waiting for recovery."
            )
        mark_production_item_created(item_id)
        recovered = recover_pending_production_credentials()
        mark_production_item_created(str(recovered["item_id"]))
        sync = sync_production_to_sqlite(database)
        return ConnectionResult(
            action="recover",
            message=(
                f"Existing bank connection recovered and synced: "
                f"{sync.account_count} account(s)."
            ),
            sync_report=sync,
        )

    if normalized == "sync":
        sync = sync_production_to_sqlite(database)
        return ConnectionResult(
            action="sync",
            message=(
                f"Sync complete: {sync.account_count} account(s), "
                f"{sync.transaction_count} stored transaction(s)."
            ),
            sync_report=sync,
        )

    raise ValueError(f"Unsupported connection action: {action}")
