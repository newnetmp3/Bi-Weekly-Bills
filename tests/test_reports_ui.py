import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from biweekly_bills.database import Database
from biweekly_bills.reports import OTHER_FINANCIAL_REPORTS, QUICK_FINANCIAL_REPORTS
from biweekly_bills.ui.reports_page import ReportsPage


class ReportsPageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_financial_reports_are_visible_buttons_without_dropdown(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            page = ReportsPage(db, auto_refresh=False)
            page.resize(1500, 900)
            page.show()
            self.app.processEvents()

            expected = dict(QUICK_FINANCIAL_REPORTS + OTHER_FINANCIAL_REPORTS)
            self.assertEqual(len(expected), 8)
            self.assertEqual(set(page.financial_report_buttons), set(expected))
            self.assertEqual(set(page.quick_report_buttons), dict(QUICK_FINANCIAL_REPORTS).keys())
            self.assertEqual(set(page.other_report_buttons), dict(OTHER_FINANCIAL_REPORTS).keys())

            for report_key, label in expected.items():
                with self.subTest(report_key=report_key):
                    button = page.financial_report_buttons[report_key]
                    self.assertEqual(button.text(), label)
                    self.assertTrue(button.isVisible())
                    self.assertTrue(button.toolTip())

            self.assertFalse(hasattr(page, "other_reports"))
            self.assertFalse(hasattr(page, "run_other_report_button"))

            page.close()


if __name__ == "__main__":
    unittest.main()
