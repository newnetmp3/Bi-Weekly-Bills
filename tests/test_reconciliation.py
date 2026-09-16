from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.database import Database
from biweekly_bills.reconciliation import (
    accept_match,
    build_preview,
    candidate_bill_instances,
    ignore_transaction,
    undo_reconciliation,
)


class ReconciliationTests(unittest.TestCase):
    def _build_fixture(self):
        temp = TemporaryDirectory()
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()

        bill_id = db.upsert_bill(
            name="Verizon",
            cycle="15th",
            payment_account="Bills Checking",
        )
        instance_id = db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Verizon",
            bill_id=bill_id,
            due_cents=21307,
            paid_cents=20000,
            method="Autopay",
            status="Partial",
            source="ods-import",
        )

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="acct-checking",
            name="Checking",
            mask="0157",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=50000,
            available_balance_cents=0,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.set_bills_checking("sandbox", "acct-checking")

        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="tx-verizon",
            plaid_account_id="acct-checking",
            posted_date="2026-09-15",
            authorized_date="2026-09-14",
            merchant_name="Verizon",
            name="VERIZON",
            amount_cents=21307,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        row = db.get_bank_transaction("sandbox", "tx-verizon")
        assert row is not None
        return temp, db, instance_id, row

    def test_accept_match_updates_paid_and_undo_restores_previous_values(self):
        temp, db, instance_id, row = self._build_fixture()
        self.addCleanup(temp.cleanup)

        preview = build_preview(db, row, instance_id)
        self.assertEqual(preview.bank_amount_cents, 21307)
        self.assertEqual(preview.due_cents, 21307)
        self.assertEqual(preview.existing_paid_cents, 20000)
        self.assertFalse(preview.already_agrees)

        accept_match(db, row, instance_id)

        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 21307)
        self.assertEqual(instance["status"], "Paid")
        self.assertEqual(instance["source"], "bank-reconciled")

        reconciled = db.get_bank_transaction("sandbox", "tx-verizon")
        assert reconciled is not None
        self.assertEqual(reconciled["reconciliation_disposition"], "matched")
        self.assertEqual(reconciled["reconciled_bill_name"], "Verizon")

        undo_reconciliation(db, reconciled)

        restored = db.get_bill_instance(instance_id)
        assert restored is not None
        self.assertEqual(restored["paid_cents"], 20000)
        self.assertEqual(restored["status"], "Partial")
        self.assertEqual(restored["source"], "ods-import")
        self.assertIsNone(db.get_reconciliation("sandbox", "tx-verizon"))

    def test_everyday_checking_payment_can_be_reconciled(self):
        temp, db, instance_id, _ = self._build_fixture()
        self.addCleanup(temp.cleanup)

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="acct-everyday",
            name="Everyday Checking",
            mask="4242",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=250000,
            available_balance_cents=240000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="tx-everyday-verizon",
            plaid_account_id="acct-everyday",
            posted_date="2026-09-15",
            authorized_date=None,
            merchant_name="Verizon",
            name="VERIZON",
            amount_cents=21307,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )

        everyday_tx = db.get_bank_transaction("sandbox", "tx-everyday-verizon")
        assert everyday_tx is not None
        self.assertEqual(everyday_tx["is_bills_checking"], 0)

        accept_match(db, everyday_tx, instance_id)

        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 21307)
        self.assertEqual(instance["status"], "Paid")
        self.assertEqual(instance["source"], "bank-reconciled")

    def test_ignore_does_not_change_bill_and_can_be_undone(self):
        temp, db, instance_id, row = self._build_fixture()
        self.addCleanup(temp.cleanup)

        ignore_transaction(db, row)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 20000)
        self.assertEqual(instance["status"], "Partial")

        ignored = db.get_bank_transaction("sandbox", "tx-verizon")
        assert ignored is not None
        self.assertEqual(ignored["reconciliation_disposition"], "ignored")

        undo_reconciliation(db, ignored)
        self.assertIsNone(db.get_reconciliation("sandbox", "tx-verizon"))

    def test_one_bill_instance_cannot_be_matched_to_two_transactions(self):
        temp, db, instance_id, row = self._build_fixture()
        self.addCleanup(temp.cleanup)

        accept_match(db, row, instance_id)

        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="tx-verizon-2",
            plaid_account_id="acct-checking",
            posted_date="2026-09-15",
            authorized_date=None,
            merchant_name="Verizon",
            name="VERIZON SECOND",
            amount_cents=21307,
            pending=False,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )
        second = db.get_bank_transaction("sandbox", "tx-verizon-2")
        assert second is not None

        with self.assertRaisesRegex(ValueError, "already reconciled"):
            accept_match(db, second, instance_id)

    def test_removed_plaid_transaction_unwinds_reconciliation(self):
        temp, db, instance_id, row = self._build_fixture()
        self.addCleanup(temp.cleanup)

        accept_match(db, row, instance_id)
        db.delete_bank_transaction("sandbox", "tx-verizon")

        restored = db.get_bill_instance(instance_id)
        assert restored is not None
        self.assertEqual(restored["paid_cents"], 20000)
        self.assertEqual(restored["status"], "Partial")
        self.assertIsNone(db.get_bank_transaction("sandbox", "tx-verizon"))
        self.assertIsNone(db.get_reconciliation("sandbox", "tx-verizon"))

    def test_candidates_are_limited_to_transaction_month_and_cycle(self):
        temp, db, instance_id, row = self._build_fixture()
        self.addCleanup(temp.cleanup)

        other_bill = db.upsert_bill(name="Cox", cycle="1st")
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Cox",
            bill_id=other_bill,
            due_cents=12060,
        )
        october_bill = db.upsert_bill(name="October Bill", cycle="15th")
        db.upsert_bill_instance(
            year=2026,
            month=10,
            cycle="15th",
            bill_name="October Bill",
            bill_id=october_bill,
            due_cents=5000,
        )

        candidates = candidate_bill_instances(db, row)
        self.assertEqual([int(candidate["id"]) for candidate in candidates], [instance_id])


if __name__ == "__main__":
    unittest.main()
