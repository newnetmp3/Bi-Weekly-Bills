import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.ui.main_window import MainWindow


class ContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_major_page_tables_use_custom_context_menus(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(
                db,
                backup_dir=Path(temp_dir) / "backups",
            )

            window = MainWindow(db, backups)
            window.resize(1400, 900)
            window.show()
            self.app.processEvents()

            tables = (
                window.dashboard.table,
                window.bills.bill_table,
                window.pay_periods.table,
                window.settings.accounts_panel.table,
                window.transactions.table,
                window.reconciliation.audit_table,
                window.reconciliation.diagnostic_table,
                window.reports.table,
                window.settings.table,
            )
            for table in tables:
                self.assertEqual(
                    table.contextMenuPolicy(),
                    Qt.ContextMenuPolicy.CustomContextMenu,
                    table.objectName() or table.__class__.__name__,
                )

            window.close()


if __name__ == "__main__":
    unittest.main()
