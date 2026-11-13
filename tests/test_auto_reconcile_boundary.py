"""Payments made just before/after a month boundary still verify the right bill."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.auto_reconcile import auto_reconcile_transactions
from biweekly_bills.database import Database


class MonthBoundaryMatchingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "bills.sqlite3")
        self.db.initialize()
        self.db.upsert_bank_account(
            environment="production",
            plaid_account_id="checking",
            name="Checking",
            mask="6386",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at="2026-11-01T00:00:00+00:00",
        )
        self.bill_id = self.db.upsert_bill(
            name="Blue (7379)", cycle="1st", payment_account_id="checking"
        )

    def _instance(self, month: int, cycle: str, paid: int) -> int:
        return self.db.upsert_bill_instance(
            year=2026,
            month=month,
            cycle=cycle,
            bill_name="Blue (7379)",
            bill_id=self.bill_id,
            due_cents=paid,
            paid_cents=paid,
            status="Paid",
        )

    def _tx(self, tx_id: str, posted: str, amount: int) -> None:
        self.db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id=tx_id,
            plaid_account_id="checking",
            posted_date=posted,
            authorized_date=None,
            merchant_name=None,
            name="Transfer to Credit Card TRF TO OTHER",
            amount_cents=amount,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-11-01T00:00:00+00:00",
        )

    def _verified_by(self, month: int) -> str | None:
        row = next(
            row
            for row in self.db.list_month_instances(2026, month, active_only=False)
            if row["bill_name_snapshot"] == "Blue (7379)"
        )
        return row["bank_transaction_id"] if int(row["bank_verified"] or 0) else None

    def test_payment_on_last_day_verifies_next_months_first_bill(self):
        self._instance(9, "1st", 50000)
        self._instance(10, "1st", 50000)
        self._tx("aug31", "2026-08-31", 50000)
        self._tx("sep30", "2026-09-30", 50000)

        auto_reconcile_transactions(self.db, "production")

        # Same amount every month: the nearer month must win each time.
        self.assertEqual(self._verified_by(9), "aug31")
        self.assertEqual(self._verified_by(10), "sep30")

    def test_far_from_boundary_payment_does_not_jump_months(self):
        self._instance(10, "1st", 50000)
        self._tx("sep10", "2026-09-10", 50000)

        auto_reconcile_transactions(self.db, "production")

        self.assertIsNone(self._verified_by(10))


if __name__ == "__main__":
    unittest.main()
