from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.production_cutover import purge_sandbox_transactions_for_production


class ProductionCutoverTests(unittest.TestCase):
    def _database(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = Database(root / "bills.sqlite3")
        db.initialize()
        backup_manager = BackupManager(
            db,
            backup_dir=root / "backups",
        )
        return root, db, backup_manager

    def test_production_cutover_purges_sandbox_transactions_with_backup(self):
        _, db, backups = self._database()

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="sandbox-checking",
            name="Sandbox Checking",
            mask="1111",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=10000,
            available_balance_cents=9000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="sandbox-tx",
            plaid_account_id="sandbox-checking",
            posted_date="2026-09-15",
            authorized_date="2026-09-14",
            merchant_name="Sandbox Merchant",
            name="Sandbox Merchant",
            amount_cents=1000,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        db.update_sync_state(
            environment="sandbox",
            transaction_cursor="sandbox-cursor",
            transactions_update_status="HISTORICAL_UPDATE_COMPLETE",
            last_sync_at="2026-09-16T00:00:00+00:00",
        )

        with patch(
            "biweekly_bills.production_cutover.load_settings",
            return_value=SimpleNamespace(environment="production"),
        ), patch(
            "biweekly_bills.production_cutover.update_local_config"
        ) as update_local:
            report = purge_sandbox_transactions_for_production(db, backups)

        self.assertEqual(report.transaction_count, 1)
        self.assertTrue(report.backup_created)
        self.assertEqual(db.list_bank_transactions("sandbox"), [])
        self.assertIsNone(db.get_sync_state("sandbox"))
        self.assertEqual(len(db.list_bank_accounts("sandbox")), 1)
        update_local.assert_called_once_with(
            "sandbox",
            transactions_cursor=None,
        )
        self.assertEqual(len(backups.list_backups()), 1)
        self.assertEqual(
            backups.list_backups()[0].reason,
            "pre production sandbox transaction purge",
        )

    def test_sandbox_mode_does_not_purge_anything(self):
        _, db, backups = self._database()

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="sandbox-checking",
            name="Sandbox Checking",
            mask="1111",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=10000,
            available_balance_cents=9000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="sandbox-tx",
            plaid_account_id="sandbox-checking",
            posted_date="2026-09-15",
            authorized_date="2026-09-14",
            merchant_name="Sandbox Merchant",
            name="Sandbox Merchant",
            amount_cents=1000,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )

        with patch(
            "biweekly_bills.production_cutover.load_settings",
            return_value=SimpleNamespace(environment="sandbox"),
        ), patch(
            "biweekly_bills.production_cutover.update_local_config"
        ) as update_local:
            report = purge_sandbox_transactions_for_production(db, backups)

        self.assertEqual(report.transaction_count, 0)
        self.assertFalse(report.backup_created)
        self.assertEqual(len(db.list_bank_transactions("sandbox")), 1)
        self.assertEqual(backups.list_backups(), [])
        update_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
