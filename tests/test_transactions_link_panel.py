"""The 'Link a payment to a bill' panel shows one clear state at a time."""

from datetime import date
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.ui.transactions_page import TransactionsPage


class LinkPanelTests(unittest.TestCase):
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
        for account_id, name, mask, kind, subtype in (
            ("checking", "Checking", "6386", "depository", "checking"),
            ("card", "Blue Card", "7379", "credit", "credit card"),
        ):
            self.db.upsert_bank_account(
                environment="production",
                plaid_account_id=account_id,
                name=name,
                mask=mask,
                account_type=kind,
                account_subtype=subtype,
                current_balance_cents=0,
                available_balance_cents=0,
                last_synced_at=f"{self.today.isoformat()}T00:00:00+00:00",
            )
        self.power = self._bill("Power Utility", 12000)
        self.water = self._bill("Water Utility", 4000)
        self.env = patch.dict(os.environ, {"PLAID_ENV": "production"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _bill(self, name: str, due: int) -> int:
        bill_id = self.db.upsert_bill(name=name, cycle="1st", payment_account_id="checking")
        return self.db.upsert_bill_instance(
            year=self.today.year,
            month=self.today.month,
            cycle="1st",
            bill_name=name,
            bill_id=bill_id,
            due_cents=due,
            status="Due",
        )

    def _tx(self, tx_id: str, name: str, amount: int, account: str = "checking") -> None:
        self.db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id=tx_id,
            plaid_account_id=account,
            posted_date=self.today.replace(day=3).isoformat(),
            authorized_date=None,
            merchant_name=None,
            name=name,
            amount_cents=amount,
            pending=False,
            raw_json="{}",
            last_seen_at=f"{self.today.isoformat()}T00:00:00+00:00",
        )

    def _page(self) -> TransactionsPage:
        page = TransactionsPage(self.db, lambda: None, self.backups)
        page.resize(1300, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)
        return page

    def _select(self, page: TransactionsPage, text: str) -> None:
        for row in range(page.table.rowCount()):
            cells = [
                page.table.item(row, column).text()
                for column in range(page.table.columnCount())
                if page.table.item(row, column) is not None
            ]
            if any(text in cell for cell in cells):
                page.table.selectRow(row)
                self.app.processEvents()
                return
        self.fail(f"{text} not found in the table")

    def _choose(self, page: TransactionsPage, instance_id: int) -> None:
        index = page.target_bill.findData(instance_id)
        self.assertGreaterEqual(index, 0)
        page.target_bill.setCurrentIndex(index)
        self.app.processEvents()

    def test_nothing_selected_shows_prompt_only(self):
        page = self._page()
        self.assertIn("Pick a payment", page.state_title.text())
        self.assertFalse(page.accept_button.isVisible())
        self.assertFalse(page.undo_button.isVisible())

    def test_link_move_and_unlink(self):
        self._tx("tx-1", "ONLINE PAYMENT 1234", 12000)
        page = self._page()
        self._select(page, "ONLINE PAYMENT")

        self.assertEqual(page.state_title.text(), "Which bill is this payment for?")
        self.assertFalse(page.accept_button.isEnabled())
        self.assertEqual(page.accept_button.text(), "Choose a bill first")

        self._choose(page, self.power)
        self.assertEqual(page.accept_button.text(), "Link to Power Utility")
        self.assertIn("exactly the amount due", page.compare_label.text())
        page.accept_button.click()
        self.app.processEvents()
        self._select(page, "ONLINE PAYMENT")

        self.assertTrue(page.state_title.text().startswith("✓ Linked to Power Utility"))
        self.assertTrue(page.change_button.isVisible())
        self.assertEqual(page.undo_button.text(), "Unlink")
        self.assertFalse(page.accept_button.isVisible())

        # Move the link to a different bill.
        page.change_button.click()
        self.app.processEvents()
        self.assertEqual(page.target_bill.findData(self.power), -1)
        self._choose(page, self.water)
        self.assertEqual(page.accept_button.text(), "Move link to Water Utility")
        page.accept_button.click()
        self.app.processEvents()
        self.assertEqual(
            int(self.db.get_reconciliation("production", "tx-1")["bill_instance_id"]),
            self.water,
        )
        self.assertIsNone(self.db.get_bill_instance(self.power)["paid_cents"])

        self._select(page, "ONLINE PAYMENT")
        page.undo_button.click()
        self.app.processEvents()
        self.assertIsNone(self.db.get_reconciliation("production", "tx-1"))
        self.assertIn("Unlinked", page.action_status.text())

    def test_not_a_bill_then_undo(self):
        self._tx("tx-2", "COFFEE SHOP", 550)
        page = self._page()
        self._select(page, "COFFEE SHOP")
        page.ignore_button.click()
        self.app.processEvents()
        self._select(page, "COFFEE SHOP")
        self.assertEqual(page.state_title.text(), "Marked as not a bill")
        self.assertEqual(page.undo_button.text(), "Undo")
        page.undo_button.click()
        self.app.processEvents()
        self.assertIsNone(self.db.get_reconciliation("production", "tx-2"))

    def test_bill_already_paid_by_another_payment_is_blocked(self):
        self._tx("tx-a", "ONLINE PAYMENT A", 12000)
        self._tx("tx-b", "ONLINE PAYMENT B", 12000)
        self.db.reconcile_transaction(
            environment="production", plaid_transaction_id="tx-a", bill_instance_id=self.power
        )
        page = self._page()
        self._select(page, "ONLINE PAYMENT B")
        self._choose(page, self.power)
        self.assertFalse(page.accept_button.isEnabled())
        self.assertIn("already has another bank payment", page.warning.text())

    def test_card_receiving_payment_explains_itself(self):
        self._tx("tx-out", "Transfer to Credit Card", 12000)
        self._tx("tx-in", "NFO PAYMENT RECEIVED", -12000, account="card")
        self.db.reconcile_transaction(
            environment="production", plaid_transaction_id="tx-out", bill_instance_id=self.power
        )
        self.db.record_internal_transfer_match(
            environment="production",
            source_plaid_transaction_id="tx-out",
            destination_plaid_transaction_id="tx-in",
            bill_instance_id=self.power,
            amount_cents=12000,
        )
        page = self._page()
        index = page.account_filter.findData("card")
        if index >= 0:
            page.account_filter.setCurrentIndex(index)
        else:
            for i in range(page.account_filter.count()):
                if "Blue Card" in page.account_filter.itemText(i):
                    page.account_filter.setCurrentIndex(i)
        self.app.processEvents()
        self._select(page, "Card received payment")
        self.assertEqual(page.state_title.text(), "Payment received for Power Utility")
        self.assertIn("Checking ••••6386", page.state_detail.text())
        self.assertEqual(page.undo_button.text(), "Unlink")


if __name__ == "__main__":
    unittest.main()
