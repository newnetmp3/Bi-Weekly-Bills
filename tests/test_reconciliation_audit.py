from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biweekly_bills.database import Database
from biweekly_bills.reconciliation_audit import (
    build_reconciliation_audit,
    run_integrity_diagnostics,
)


class ReconciliationAuditTests(unittest.TestCase):
    def _db(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        return db

    def test_paid_historical_bill_surfaces_high_confidence_candidate(self):
        db = self._db()
        bill_id = db.upsert_bill(
            name="Variable Card",
            cycle="15th",
        )
        instance_id = db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Variable Card",
            bill_id=bill_id,
            due_cents=15000,
            paid_cents=23741,
            status="Paid",
        )
        db.set_bill_manually_paid(instance_id, True)
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="tx-variable",
            plaid_account_id=None,
            posted_date="2026-09-18",
            authorized_date=None,
            merchant_name="Variable Card",
            name="VARIABLE CARD",
            amount_cents=23741,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-10-01T00:00:00+00:00",
        )

        audit = build_reconciliation_audit(
            db,
            "production",
            today=date(2026, 10, 1),
        )

        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0].state, "Needs review")
        self.assertEqual(
            audit[0].candidate_transaction_id,
            "tx-variable",
        )
        self.assertIn("Reconcile history", audit[0].reason)

    def test_paid_historical_bill_uses_generic_configured_account_outflow(self):
        db = self._db()
        db.upsert_bank_account(
            environment="production",
            plaid_account_id="bills",
            name="Bills Checking",
            mask="1111",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at="2026-10-01T00:00:00+00:00",
        )
        bill_id = db.upsert_bill(
            name="Star Card",
            cycle="15th",
            payment_account_id="bills",
        )
        instance_id = db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Star Card",
            bill_id=bill_id,
            due_cents=12750,
            paid_cents=12750,
            status="Paid",
        )
        db.set_bill_manually_paid(instance_id, True)
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="tx-star-generic",
            plaid_account_id="bills",
            posted_date="2026-09-16",
            authorized_date=None,
            merchant_name="ACH PAYMENT",
            name="ACH PAYMENT",
            amount_cents=12750,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-10-01T00:00:00+00:00",
        )

        audit = build_reconciliation_audit(
            db,
            "production",
            today=date(2026, 10, 1),
        )

        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0].state, "Needs review")
        self.assertEqual(
            audit[0].candidate_transaction_id,
            "tx-star-generic",
        )
        self.assertIn("configured Payment Account", audit[0].reason)

    def test_paid_historical_bill_without_candidate_is_paid_unverified(self):
        db = self._db()
        bill_id = db.upsert_bill(
            name="Cash Bill",
            cycle="1st",
        )
        instance_id = db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Cash Bill",
            bill_id=bill_id,
            due_cents=4500,
            paid_cents=4500,
            status="Paid",
        )
        db.set_bill_manually_paid(instance_id, True)

        audit = build_reconciliation_audit(
            db,
            "production",
            today=date(2026, 10, 1),
        )

        self.assertEqual(audit[0].state, "Paid · unverified")
        self.assertIn("no sufficiently similar", audit[0].reason)

    def test_diagnostics_reuse_prebuilt_audit(self):
        db = self._db()
        prebuilt = build_reconciliation_audit(
            db,
            "production",
            today=date(2026, 10, 1),
        )

        with patch(
            "biweekly_bills.reconciliation_audit.build_reconciliation_audit",
            side_effect=AssertionError("audit should be reused"),
        ):
            issues = run_integrity_diagnostics(
                db,
                "production",
                today=date(2026, 10, 1),
                audit_entries=prebuilt,
            )

        self.assertIsInstance(issues, list)

    def test_diagnostics_find_unpaired_credit_receipt_and_missing_account(self):
        db = self._db()
        db.upsert_bill(
            name="Missing Account Bill",
            cycle="1st",
            payment_account_id="missing-account",
        )
        db.upsert_bank_account(
            environment="production",
            plaid_account_id="green-card",
            name="Green Card",
            mask=None,
            account_type="credit",
            account_subtype="credit card",
            current_balance_cents=10000,
            available_balance_cents=None,
            last_synced_at="2026-09-20T00:00:00+00:00",
        )
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="nfo-unpaired",
            plaid_account_id="green-card",
            posted_date="2026-09-20",
            authorized_date=None,
            merchant_name="NFO PAYMENT RECEIVED",
            name="TRANSFER CREDIT",
            amount_cents=-12000,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-10-01T00:00:00+00:00",
        )

        issues = run_integrity_diagnostics(
            db,
            "production",
            today=date(2026, 10, 1),
        )
        codes = {issue.code for issue in issues}

        self.assertIn("missing-payment-account", codes)
        self.assertIn("unpaired-credit-receipt", codes)


if __name__ == "__main__":
    unittest.main()
