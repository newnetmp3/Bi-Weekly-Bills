import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.ui.pay_periods import PayPeriodsPage, bank_status_text


class PayPeriodLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_historical_manual_payment_is_not_labeled_waiting_for_bank(self):
        self.assertEqual(
            bank_status_text(
                manually_paid=True,
                bank_verified=False,
                year=2026,
                month=8,
                today=date(2026, 9, 16),
            ),
            "Paid · unverified",
        )
        self.assertEqual(
            bank_status_text(
                manually_paid=True,
                bank_verified=False,
                year=2026,
                month=9,
                today=date(2026, 9, 16),
            ),
            "Waiting for bank",
        )
        self.assertEqual(
            bank_status_text(
                manually_paid=True,
                bank_verified=True,
                year=2026,
                month=8,
                today=date(2026, 9, 16),
            ),
            "✓ Verified",
        )

    def test_inactive_bills_do_not_appear_or_count_in_progress(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(db, backup_dir=Path(temp_dir) / "backups")

            active_id = db.upsert_bill(
                name="Active Bill",
                cycle="1st",
                active=True,
            )
            inactive_id = db.upsert_bill(
                name="Inactive Bill",
                cycle="1st",
                active=True,
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Active Bill",
                bill_id=active_id,
                due_cents=5000,
                status="Due",
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Inactive Bill",
                bill_id=inactive_id,
                due_cents=6000,
                status="Due",
            )
            db.set_bill_active(inactive_id, False)

            page = PayPeriodsPage(db, lambda: None, backups)
            page.resize(1200, 820)
            page.show()
            page.set_view("1st")
            self.app.processEvents()

            self.assertEqual(page.table.rowCount(), 1)
            self.assertIn("Active Bill", page.table.item(0, 1).text())
            self.assertNotIn("Inactive Bill", page.table.item(0, 1).text())
            self.assertEqual(page.workflow_progress.maximum(), 1)
            self.assertEqual(page.workflow_progress.value(), 0)
            self.assertIn("0 of 1 handled", page.workflow_summary.text())

            page.close()

    def test_selected_row_becomes_inline_editable_and_saves(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(
                db,
                backup_dir=Path(temp_dir) / "backups",
            )

            bill_id = db.upsert_bill(
                name="Inline Edit Bill",
                cycle="1st",
                active=True,
            )
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Inline Edit Bill",
                bill_id=bill_id,
                when_label="Sep 10",
                due_cents=12345,
                paid_cents=None,
                method="Autopay",
                status="Due",
                extra_short="0.00",
            )

            page = PayPeriodsPage(db, lambda: None, backups)
            page.resize(1400, 820)
            page.show()
            page.month.setCurrentIndex(8)
            page.year.setCurrentText("2026")
            page.set_view("1st")
            self.app.processEvents()

            self.assertFalse(hasattr(page, "editor"))
            self.assertEqual(page.table.columnCount(), 11)
            self.assertEqual(page.table.rowCount(), 1)

            page.table.selectRow(0)
            self.app.processEvents()

            editable_columns = {3, 4, 5, 6, 9, 10}
            self.assertGreaterEqual(page.table.rowHeight(0), 40)
            expected_widths = {
                3: 100,
                4: 96,
                5: 108,
                6: 118,
                9: 108,
                10: 100,
            }
            for column, minimum in expected_widths.items():
                self.assertGreaterEqual(
                    page.table.columnWidth(column),
                    minimum,
                    f"column {column} should be wide enough for inline editing",
                )

            for column in range(page.table.columnCount()):
                widget = page.table.cellWidget(0, column)
                if column in editable_columns:
                    self.assertIsInstance(
                        widget,
                        QLineEdit,
                        f"column {column} should have an inline editor",
                    )
                    self.assertGreaterEqual(widget.minimumHeight(), 30)
                    self.assertGreaterEqual(
                        widget.minimumWidth(),
                        expected_widths[column],
                    )
                else:
                    self.assertIsNone(
                        widget,
                        f"column {column} should remain read-only",
                    )

            due_editor = page.table.cellWidget(0, 4)
            assert isinstance(due_editor, QLineEdit)
            due_editor.setText("130.00")
            due_editor.editingFinished.emit()
            self.app.processEvents()

            row = db.get_bill_instance(instance_id)
            assert row is not None
            self.assertEqual(row["due_cents"], 13000)
            self.assertEqual(page.selected_instance_id, instance_id)
            self.assertIn("saved", page.workflow_status.text().casefold())

            page.close()


if __name__ == "__main__":
    unittest.main()
