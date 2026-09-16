import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSizePolicy

from biweekly_bills.database import Database
from biweekly_bills.ui.main_window import DashboardPage
from biweekly_bills.ui.overview_chart import DonutBreakdownChart


class OverviewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_overview_is_compact_and_charts_use_real_month_totals(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            first_bill = db.upsert_bill(
                name="First Bill",
                cycle="1st",
                active=True,
            )
            fifteenth_bill = db.upsert_bill(
                name="Fifteenth Bill",
                cycle="15th",
                active=True,
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="First Bill",
                bill_id=first_bill,
                due_cents=10000,
                paid_cents=7500,
                status="Partial",
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Fifteenth Bill",
                bill_id=fifteenth_bill,
                due_cents=30000,
                paid_cents=5000,
                status="Partial",
            )

            page = DashboardPage(db)
            page.resize(1500, 900)
            page.show()
            page.year_selector.setCurrentText("2026")
            page.month_selector.setCurrentIndex(8)
            page.refresh()
            self.app.processEvents()

            self.assertIsInstance(
                page.scheduled_chart,
                DonutBreakdownChart,
            )
            self.assertIsInstance(
                page.paid_chart,
                DonutBreakdownChart,
            )
            self.assertIn(
                "1st Pay Period: 10000",
                page.scheduled_chart.accessibleName(),
            )
            self.assertIn(
                "15th Pay Period: 30000",
                page.scheduled_chart.accessibleName(),
            )
            self.assertIn(
                "25% 1st share",
                page.scheduled_chart.accessibleName(),
            )
            self.assertIn(
                "Paid: 12500",
                page.paid_chart.accessibleName(),
            )
            self.assertIn(
                "Still due: 27500",
                page.paid_chart.accessibleName(),
            )
            self.assertIn(
                "31% of scheduled",
                page.paid_chart.accessibleName(),
            )

            self.assertEqual(
                page.hero.sizePolicy().verticalPolicy(),
                QSizePolicy.Policy.Maximum,
            )
            self.assertLess(page.hero.sizeHint().height(), 260)
            self.assertLess(page.first_card.sizeHint().height(), 210)
            self.assertLess(page.fifteenth_card.sizeHint().height(), 210)

            page.close()


if __name__ == "__main__":
    unittest.main()
