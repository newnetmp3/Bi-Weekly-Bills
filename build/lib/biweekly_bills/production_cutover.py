from __future__ import annotations

from dataclasses import dataclass

from .backups import BackupManager
from .database import Database
from .local_config import update_local_config
from .settings import load_settings


@dataclass(frozen=True)
class ProductionCutoverCleanup:
    transaction_count: int
    backup_created: bool


def purge_sandbox_transactions_for_production(
    database: Database,
    backup_manager: BackupManager,
) -> ProductionCutoverCleanup:
    """Remove Sandbox transaction/reconciliation state before real-bank use.

    The cleanup only runs while PLAID_ENV=production. Sandbox account rows are
    retained for audit/reference, but their transaction cache, reconciliation
    state, sync cursor, and legacy cursor are removed so Production UI/reporting
    cannot accidentally surface test activity.
    """

    settings = load_settings(require_keys=False)
    if settings.environment != "production":
        return ProductionCutoverCleanup(
            transaction_count=0,
            backup_created=False,
        )

    sandbox_transactions = database.list_bank_transactions(
        "sandbox",
        limit=1_000_000,
    )
    count = len(sandbox_transactions)
    backup_created = False

    if count:
        backup_manager.create_backup(
            "pre-production-sandbox-transaction-purge"
        )
        backup_created = True

    database.clear_bank_transactions(
        "sandbox",
        clear_sync_state=True,
    )
    update_local_config("sandbox", transactions_cursor=None)

    return ProductionCutoverCleanup(
        transaction_count=count,
        backup_created=backup_created,
    )
