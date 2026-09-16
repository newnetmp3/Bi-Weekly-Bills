import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.ui.reconciliation_page import ReconciliationPage


class UiResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_until(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return bool(predicate())

    def test_reconciliation_audit_runs_off_gui_thread_and_filters_stay_live(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            backups = BackupManager(
                db,
                backup_dir=Path(temp_dir) / "backups",
            )
            page = ReconciliationPage(
                db,
                lambda: None,
                backups,
                auto_refresh=False,
            )
            page.resize(1200, 820)
            page.show()
            self.app.processEvents()

            started = Event()
            release = Event()

            def slow_audit(*_args, **_kwargs):
                started.set()
                release.wait(2.0)
                return []

            with patch(
                "biweekly_bills.ui.reconciliation_page.build_reconciliation_audit",
                side_effect=slow_audit,
            ), patch(
                "biweekly_bills.ui.reconciliation_page.run_integrity_diagnostics",
                return_value=[],
            ):
                page.refresh()

                self.assertTrue(started.wait(1.0))
                self.assertIsNotNone(page._audit_thread)
                self.assertFalse(page.refresh_button.isEnabled())
                self.assertFalse(page.reconcile_button.isEnabled())

                # This executes on the GUI thread while the worker is stalled.
                page.search.setText("still responsive")
                self.app.processEvents()
                self.assertEqual(
                    page.search.text(),
                    "still responsive",
                )

                release.set()
                self.assertTrue(
                    self._wait_until(
                        lambda: page._audit_thread is None,
                    )
                )

            self.assertTrue(page.refresh_button.isEnabled())
            self.assertTrue(page.reconcile_button.isEnabled())
            page.close()


if __name__ == "__main__":
    unittest.main()
