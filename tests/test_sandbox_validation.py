from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biweekly_bills.database import Database
from biweekly_bills import sandbox_validation
from biweekly_bills.sandbox_validation import (
    record_sandbox_validation_pass,
    run_sandbox_validation,
)


class SandboxValidationTests(unittest.TestCase):
    def _fixture(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = Database(root / "bills.sqlite3")
        db.initialize()

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="sandbox-checking",
            name="Sandbox Checking",
            mask="0157",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=50000,
            available_balance_cents=45000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.set_bills_checking("sandbox", "sandbox-checking")
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="sandbox-existing-tx",
            plaid_account_id="sandbox-checking",
            posted_date="2026-09-10",
            authorized_date="2026-09-09",
            merchant_name="Existing Sandbox Merchant",
            name="EXISTING SANDBOX TRANSACTION",
            amount_cents=2500,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        return db

    def test_offline_validation_exercises_local_pipeline_without_mutating_source(self):
        db = self._fixture()
        before_bills = [tuple(row) for row in db.list_bills(active_only=False)]
        before_transactions = [
            tuple(row) for row in db.list_bank_transactions("sandbox", limit=1000)
        ]

        with patch(
            "biweekly_bills.sandbox_validation.sandbox_connection_status",
            return_value={
                "configured_environment": "sandbox",
                "keys_configured": False,
                "credential_present": False,
                "item_id": None,
            },
        ):
            report = run_sandbox_validation(
                db,
                perform_live_sync=False,
            )

        self.assertTrue(report.passed)
        self.assertEqual(report.fail_count, 0)
        self.assertGreaterEqual(report.pass_count, 7)
        self.assertGreaterEqual(report.skip_count, 2)

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["Isolated database clone"].status, "PASS")
        self.assertEqual(checks["Sandbox cache"].status, "PASS")
        self.assertEqual(checks["Reconciliation round-trip"].status, "PASS")
        self.assertEqual(checks["Funding Sandbox gate"].status, "PASS")
        self.assertEqual(checks["Funding calculation"].status, "PASS")
        self.assertEqual(checks["Backup / restore round-trip"].status, "PASS")
        self.assertEqual(checks["Report export round-trip"].status, "PASS")
        self.assertEqual(checks["Working database unchanged"].status, "PASS")

        self.assertEqual(
            [tuple(row) for row in db.list_bills(active_only=False)],
            before_bills,
        )
        self.assertEqual(
            [tuple(row) for row in db.list_bank_transactions("sandbox", limit=1000)],
            before_transactions,
        )

    def test_successful_validation_pass_can_be_recorded_for_production_readiness(self):
        db = self._fixture()
        with patch(
            "biweekly_bills.sandbox_validation.sandbox_connection_status",
            return_value={
                "configured_environment": "sandbox",
                "keys_configured": False,
                "credential_present": False,
                "item_id": None,
            },
        ):
            report = run_sandbox_validation(
                db,
                perform_live_sync=False,
            )

        self.assertTrue(report.passed)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            marker_path = root / "sandbox_validation_pass.json"
            with patch.object(sandbox_validation, "APP_DIR", root), patch.object(
                sandbox_validation,
                "SANDBOX_VALIDATION_MARKER",
                marker_path,
            ):
                recorded = record_sandbox_validation_pass(report)
                loaded = sandbox_validation.load_sandbox_validation_marker()

            self.assertEqual(recorded, marker_path)
            self.assertTrue(loaded["passed"])
            self.assertEqual(loaded["fail_count"] if "fail_count" in loaded else 0, 0)
            self.assertEqual(marker_path.stat().st_mode & 0o777, 0o600)

    def test_existing_production_rows_are_only_a_warning_and_are_not_modified(self):
        db = self._fixture()
        db.upsert_bank_account(
            environment="production",
            plaid_account_id="prod-existing",
            name="Existing Production Row",
            mask="9999",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=10000,
            available_balance_cents=9000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )

        with patch(
            "biweekly_bills.sandbox_validation.sandbox_connection_status",
            return_value={
                "configured_environment": "sandbox",
                "keys_configured": False,
                "credential_present": False,
                "item_id": None,
            },
        ):
            report = run_sandbox_validation(
                db,
                perform_live_sync=False,
            )

        self.assertTrue(report.passed)
        production_check = next(
            check for check in report.checks if check.name == "Production isolation"
        )
        self.assertEqual(production_check.status, "WARN")
        production_accounts = db.list_bank_accounts("production")
        self.assertEqual(len(production_accounts), 1)
        self.assertEqual(production_accounts[0]["plaid_account_id"], "prod-existing")


if __name__ == "__main__":
    unittest.main()
