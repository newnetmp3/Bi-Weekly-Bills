import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.ui.transactions_page import TransactionsPage


class TransactionsPeriodFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_month_survives_signal_refresh_and_manual_refresh(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(db, backup_dir=Path(temp_dir) / "backups")

            db.upsert_bank_account(
                environment="sandbox",
                plaid_account_id="checking",
                name="Checking",
                mask="1234",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            for tx_id, posted_date in (
                ("jan-tx", "2001-01-10"),
                ("feb-tx", "2001-02-10"),
            ):
                db.upsert_bank_transaction(
                    environment="sandbox",
                    plaid_transaction_id=tx_id,
                    plaid_account_id="checking",
                    posted_date=posted_date,
                    authorized_date=None,
                    merchant_name="Test Merchant",
                    name="TEST TRANSACTION",
                    amount_cents=2500,
                    pending=False,
                    raw_json="{}",
                    last_seen_at="2026-09-16T00:00:00+00:00",
                )

            with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
                page = TransactionsPage(db, lambda: None, backups)
                page.resize(1200, 820)
                page.show()
                self.app.processEvents()

                january_index = page.period_filter.findText("January 2001")
                self.assertGreaterEqual(january_index, 0)

                page.period_filter.setCurrentIndex(january_index)
                self.app.processEvents()

                self.assertEqual(page.period_filter.currentText(), "January 2001")
                self.assertEqual(page.table.rowCount(), 1)
                self.assertEqual(page.table.item(0, 0).text(), "2001-01-10")

                page.refresh()
                self.app.processEvents()

                self.assertEqual(page.period_filter.currentText(), "January 2001")
                self.assertEqual(page.table.rowCount(), 1)
                self.assertEqual(page.table.item(0, 0).text(), "2001-01-10")

                page.close()

    def test_all_dates_persists_and_local_filters_do_not_reload_bank_rows(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(
                db,
                backup_dir=Path(temp_dir) / "backups",
            )
            db.upsert_bank_account(
                environment="sandbox",
                plaid_account_id="checking",
                name="Checking",
                mask="1234",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            for tx_id, posted_date in (
                ("jan-tx", "2001-01-10"),
                ("feb-tx", "2001-02-10"),
            ):
                db.upsert_bank_transaction(
                    environment="sandbox",
                    plaid_transaction_id=tx_id,
                    plaid_account_id="checking",
                    posted_date=posted_date,
                    authorized_date=None,
                    merchant_name=(
                        "January Merchant"
                        if tx_id == "jan-tx"
                        else "February Merchant"
                    ),
                    name="TEST TRANSACTION",
                    amount_cents=2500,
                    pending=False,
                    raw_json="{}",
                    last_seen_at="2026-09-16T00:00:00+00:00",
                )

            with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
                page = TransactionsPage(db, lambda: None, backups)
                all_index = page.period_filter.findText("All dates")
                self.assertGreaterEqual(all_index, 0)

                with patch.object(
                    db,
                    "list_bank_transactions",
                    wraps=db.list_bank_transactions,
                ) as bank_rows:
                    page.period_filter.setCurrentIndex(all_index)
                    self.app.processEvents()

                    self.assertEqual(
                        page.period_filter.currentText(),
                        "All dates",
                    )
                    self.assertEqual(page.table.rowCount(), 2)
                    self.assertGreaterEqual(bank_rows.call_count, 1)
                    call = bank_rows.call_args_list[-1]
                    self.assertNotIn("year", call.kwargs)
                    self.assertNotIn("month", call.kwargs)

                    bank_rows.reset_mock()
                    page.search.setText("january")
                    page._apply_filters()
                    self.assertEqual(page.table.rowCount(), 1)
                    bank_rows.assert_not_called()

                    page.search.clear()
                    page.state_filter.setCurrentIndex(
                        page.state_filter.findData("all")
                    )
                    bank_rows.assert_not_called()

                page.close()

    def test_selecting_unresolved_transaction_loads_review_candidate(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(
                db,
                backup_dir=Path(temp_dir) / "backups",
            )

            db.upsert_bank_account(
                environment="sandbox",
                plaid_account_id="checking",
                name="Checking",
                mask="1234",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            bill_id = db.upsert_bill(
                name="Power Utility",
                cycle="15th",
            )
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Power Utility",
                bill_id=bill_id,
                due_cents=10000,
                status="Due",
            )
            db.upsert_bank_transaction(
                environment="sandbox",
                plaid_transaction_id="select-review-tx",
                plaid_account_id="checking",
                posted_date="2026-09-16",
                authorized_date=None,
                merchant_name="Unmapped Payment",
                name="UNMAPPED PAYMENT",
                amount_cents=12000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )

            assessment = SimpleNamespace(
                bill_instance_id=instance_id,
                bill_name="Power Utility",
            )
            with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}), patch(
                "biweekly_bills.ui.transactions_page.suggested_bill_instance",
                return_value=None,
            ), patch(
                "biweekly_bills.ui.transactions_page.best_review_candidate",
                return_value=(assessment, False),
            ) as review_candidate:
                page = TransactionsPage(db, lambda: None, backups)
                page.resize(1200, 820)
                page.show()
                self.app.processEvents()

                self.assertEqual(page.table.rowCount(), 1)
                page.table.selectRow(0)
                self.app.processEvents()

                review_candidate.assert_called()
                self.assertEqual(
                    page.selected_transaction_id,
                    "select-review-tx",
                )
                self.assertEqual(
                    page.target_bill.currentData(),
                    instance_id,
                )
                page.close()

    def test_needs_review_filter_surfaces_plausible_unmatched_payment(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(db, backup_dir=Path(temp_dir) / "backups")

            db.upsert_bank_account(
                environment="sandbox",
                plaid_account_id="checking",
                name="Checking",
                mask="1234",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            bill_id = db.upsert_bill(
                name="Power Utility",
                cycle="15th",
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Power Utility",
                bill_id=bill_id,
                due_cents=10000,
                status="Due",
            )
            db.upsert_bank_transaction(
                environment="sandbox",
                plaid_transaction_id="review-tx",
                plaid_account_id="checking",
                posted_date="2026-09-16",
                authorized_date=None,
                merchant_name="Power Utility",
                name="POWER UTILITY",
                amount_cents=12000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )

            with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
                page = TransactionsPage(db, lambda: None, backups)
                page.resize(1200, 820)
                page.show()
                self.app.processEvents()

                review_index = page.match_filter.findData("review")
                self.assertGreaterEqual(review_index, 0)
                page.match_filter.setCurrentIndex(review_index)
                self.app.processEvents()

                self.assertEqual(page.table.rowCount(), 1)
                self.assertIn(
                    "Needs review",
                    page.table.item(0, 6).text(),
                )
                self.assertEqual(
                    page.table.item(0, 5).text(),
                    "Power Utility",
                )
                page.close()



if __name__ == "__main__":
    unittest.main()
