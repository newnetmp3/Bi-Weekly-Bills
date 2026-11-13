"""One transfer into Bills Checking can be assigned to a group of bills."""

from datetime import date
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from biweekly_bills.auto_reconcile import auto_reconcile_transactions
from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.funding_transfer import (
    bills_funding_pairs,
    funding_targets,
)
from biweekly_bills.ui.transactions_page import TransactionsPage


class BillsFundingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.today = date.today()
        self.db = Database(Path(temp.name) / "bills.sqlite3")
        self.db.initialize()
        self.backups = BackupManager(self.db, backup_dir=Path(temp.name) / "backups")
        for account_id, name, mask in (("main", "Main", "6386"), ("bills", "Bills", "7198")):
            self.db.upsert_bank_account(
                environment="production",
                plaid_account_id=account_id,
                name=name,
                mask=mask,
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=0,
                available_balance_cents=0,
                last_synced_at=f"{self.today.isoformat()}T00:00:00+00:00",
            )
        self.db.set_bills_checking("production", "bills")
        for name, due in (("Verizon", 21300), ("Cox", 12060)):
            bill_id = self.db.upsert_bill(
                name=name, cycle="15th", payment_account_id="bills", transfer_required=True
            )
            self.db.upsert_bill_instance(
                year=self.today.year,
                month=self.today.month,
                cycle="15th",
                bill_name=name,
                bill_id=bill_id,
                due_cents=due,
                status="Autopay",
            )
        env = patch.dict(os.environ, {"PLAID_ENV": "production"})
        env.start()
        self.addCleanup(env.stop)

    def _tx(self, tx_id, account, amount, name, day=15):
        self.db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id=tx_id,
            plaid_account_id=account,
            posted_date=self.today.replace(day=day).isoformat(),
            authorized_date=None,
            merchant_name=None,
            name=name,
            amount_cents=amount,
            pending=False,
            raw_json="{}",
            last_seen_at=f"{self.today.isoformat()}T00:00:00+00:00",
        )

    def _transfer(self, amount=33360):
        self._tx("out", "main", amount, "Transfer to Checking TRF TO OTHER")
        self._tx("in", "bills", -amount, "Transfer from Checking TFR FR OTHER")

    def test_outgoing_transfer_pairs_with_bills_deposit(self):
        self._transfer()
        rows = self.db.list_bank_transactions("production", limit=100)
        pairs = bills_funding_pairs(rows)
        self.assertEqual(str(pairs["out"]["plaid_transaction_id"]), "in")

    def test_best_group_is_the_15th_period_bills(self):
        self._transfer()
        deposit = self.db.get_bank_transaction("production", "in")
        best = funding_targets(self.db, deposit)[0]
        self.assertEqual(best.scope, "15th")
        self.assertEqual(best.expected_cents, 33360)
        self.assertEqual({name for name, _ in best.bills}, {"Verizon", "Cox"})

    def test_funding_transfer_is_never_matched_to_a_bill(self):
        self._transfer(21300)  # same amount as Verizon
        auto_reconcile_transactions(self.db, "production")
        self.assertIsNone(self.db.get_reconciliation("production", "out"))

    def test_assign_from_outgoing_side(self):
        self._transfer()
        page = TransactionsPage(self.db, lambda: None, self.backups)
        self.addCleanup(page.close)
        page.show()
        page.account_filter.setCurrentIndex(page.account_filter.findData("main"))
        self.app.processEvents()
        page.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(page.funding_card.isVisible())
        self.assertFalse(page.reconcile_card.isVisible())
        self.assertEqual(page.funding_state_title.text(), "Which bills does this transfer cover?")
        self.assertIn("Verizon", page.funding_covers_label.text())
        self.assertIn("exactly enough", page.funding_compare_label.text())
        self.assertEqual(page.validate_funding_button.text(), "Assign to 15th period bills")

        page.validate_funding_button.click()
        self.app.processEvents()
        deposit = self.db.get_bank_transaction("production", "in")
        self.assertEqual(deposit["funding_validation_scope"], "15th")
        self.assertTrue(page.funding_state_title.text().startswith("✓ Covers the 15th period bills"))
        self.assertIn("Bills funding →", page.table.item(0, 6).text())

        page.undo_funding_button.click()
        self.app.processEvents()
        deposit = self.db.get_bank_transaction("production", "in")
        self.assertIsNone(deposit["funding_validation_scope"])


class MonthEndFundingTests(unittest.TestCase):
    def test_month_end_deposit_can_fund_next_months_first_period(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "bills.sqlite3")
            db.initialize()
            db.upsert_bank_account(
                environment="production",
                plaid_account_id="bills",
                name="Bills",
                mask="7198",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=0,
                available_balance_cents=0,
                last_synced_at="2026-08-31T00:00:00+00:00",
            )
            db.set_bills_checking("production", "bills")
            bill_id = db.upsert_bill(
                name="Star Card", cycle="1st", payment_account_id="bills", transfer_required=True
            )
            db.upsert_bill_instance(
                year=2026, month=9, cycle="1st", bill_name="Star Card",
                bill_id=bill_id, due_cents=13000, status="Due",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="in",
                plaid_account_id="bills",
                posted_date="2026-08-31",
                authorized_date=None,
                merchant_name=None,
                name="Transfer from Checking",
                amount_cents=-13000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-08-31T00:00:00+00:00",
            )
            deposit = db.get_bank_transaction("production", "in")
            best = funding_targets(db, deposit)[0]
            self.assertEqual((best.year, best.month, best.scope), (2026, 9, "1st"))


if __name__ == "__main__":
    unittest.main()
