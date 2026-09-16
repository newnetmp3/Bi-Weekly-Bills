from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.database import Database
from biweekly_bills.funding import scheduled_transfer_requirement
from biweekly_bills.funding_transfer import (
    build_funding_transfer_preview,
    suggested_funding_scope,
    undo_funding_transfer_validation,
    validate_funding_transfer,
)


class FundingTransferValidationTests(unittest.TestCase):
    def _database(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()

        db.upsert_bank_account(
            environment="production",
            plaid_account_id="bills",
            name="Bills",
            mask="1111",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=90000,
            available_balance_cents=90000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.set_bills_checking("production", "bills")
        return db

    def _bill(
        self,
        db,
        *,
        name,
        cycle,
        due_cents,
        paid_cents=None,
        transfer_required=True,
    ):
        bill_id = db.upsert_bill(
            name=name,
            cycle=cycle,
            transfer_required=transfer_required,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle=cycle,
            bill_name=name,
            bill_id=bill_id,
            due_cents=due_cents,
            paid_cents=paid_cents,
            status="Paid" if paid_cents else "Due",
        )

    def _incoming(self, db, *, amount_cents=-30000, tx_id="funding-transfer"):
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id=tx_id,
            plaid_account_id="bills",
            posted_date="2026-09-14",
            authorized_date="2026-09-14",
            merchant_name="Transfer",
            name="TRANSFER",
            amount_cents=amount_cents,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        row = db.get_bank_transaction("production", tx_id)
        assert row is not None
        return row

    def test_scheduled_target_uses_due_amount_even_after_autopay_posts(self):
        db = self._database()
        self._bill(
            db,
            name="Autopay A",
            cycle="1st",
            due_cents=10000,
            paid_cents=10000,
        )
        self._bill(
            db,
            name="Autopay B",
            cycle="1st",
            due_cents=20000,
            paid_cents=None,
        )
        self._bill(
            db,
            name="Direct Bill",
            cycle="1st",
            due_cents=50000,
            transfer_required=False,
        )

        self.assertEqual(
            scheduled_transfer_requirement(db, 2026, 9, "1st"),
            30000,
        )

    def test_one_large_transfer_can_validate_exact_aggregate(self):
        db = self._database()
        self._bill(db, name="Autopay A", cycle="1st", due_cents=10000)
        self._bill(db, name="Autopay B", cycle="1st", due_cents=20000)
        row = self._incoming(db, amount_cents=-30000)

        preview = build_funding_transfer_preview(db, row, "1st")
        self.assertEqual(preview.expected_cents, 30000)
        self.assertEqual(preview.actual_cents, 30000)
        self.assertEqual(preview.difference_cents, 0)

        validate_funding_transfer(db, row, "1st")
        saved = db.get_funding_transfer_validation(
            "production",
            "funding-transfer",
        )
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["scope"], "1st")
        self.assertEqual(saved["expected_cents"], 30000)
        self.assertEqual(saved["actual_cents"], 30000)
        self.assertEqual(saved["difference_cents"], 0)

        refreshed = db.get_bank_transaction(
            "production",
            "funding-transfer",
        )
        self.assertEqual(refreshed["funding_validation_scope"], "1st")

    def test_short_or_over_transfer_is_saved_with_difference(self):
        db = self._database()
        self._bill(db, name="Autopay A", cycle="15th", due_cents=25000)
        row = self._incoming(db, amount_cents=-24000)

        preview = validate_funding_transfer(db, row, "15th")
        self.assertEqual(preview.difference_cents, -1000)

        saved = db.get_funding_transfer_validation(
            "production",
            "funding-transfer",
        )
        self.assertEqual(saved["difference_cents"], -1000)

    def test_closest_scheduled_scope_is_suggested_not_transaction_day(self):
        db = self._database()
        self._bill(db, name="First", cycle="1st", due_cents=10000)
        self._bill(db, name="Fifteenth", cycle="15th", due_cents=40000)

        # Posted on the 14th, but amount clearly matches the 15th aggregate.
        row = self._incoming(db, amount_cents=-40000)
        self.assertEqual(suggested_funding_scope(db, row), "15th")

    def test_validation_does_not_mark_any_bill_paid(self):
        db = self._database()
        self._bill(db, name="Autopay A", cycle="1st", due_cents=10000)
        before = db.list_cycle_instances(2026, 9, "1st")[0]
        self.assertIsNone(before["paid_cents"])

        row = self._incoming(db, amount_cents=-10000)
        validate_funding_transfer(db, row, "1st")

        after = db.list_cycle_instances(2026, 9, "1st")[0]
        self.assertIsNone(after["paid_cents"])
        self.assertNotEqual(after["source"], "bank-reconciled")

    def test_validation_requires_incoming_transaction_to_bills_checking(self):
        db = self._database()
        self._bill(db, name="Autopay A", cycle="1st", due_cents=10000)

        db.upsert_bank_account(
            environment="production",
            plaid_account_id="everyday",
            name="Primary Checking",
            mask="2222",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="wrong-account",
            plaid_account_id="everyday",
            posted_date="2026-09-14",
            authorized_date="2026-09-14",
            merchant_name="Transfer",
            name="TRANSFER",
            amount_cents=-10000,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        row = db.get_bank_transaction("production", "wrong-account")
        with self.assertRaisesRegex(ValueError, "designated Bills Checking"):
            validate_funding_transfer(db, row, "1st")

    def test_undo_and_transaction_removal_delete_validation_only(self):
        db = self._database()
        self._bill(db, name="Autopay A", cycle="1st", due_cents=10000)
        row = self._incoming(db, amount_cents=-10000)

        validate_funding_transfer(db, row, "1st")
        undo_funding_transfer_validation(db, row)
        self.assertIsNone(
            db.get_funding_transfer_validation(
                "production",
                "funding-transfer",
            )
        )

        row = self._incoming(
            db,
            amount_cents=-10000,
            tx_id="funding-transfer-2",
        )
        validate_funding_transfer(db, row, "1st")
        db.delete_bank_transaction("production", "funding-transfer-2")
        self.assertIsNone(
            db.get_funding_transfer_validation(
                "production",
                "funding-transfer-2",
            )
        )


if __name__ == "__main__":
    unittest.main()
