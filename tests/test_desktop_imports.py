import unittest


class DesktopImportTests(unittest.TestCase):
    def test_desktop_modules_import_without_starting_qt(self):
        import biweekly_bills.desktop_app as desktop_app
        from biweekly_bills.desktop_integration import install_desktop_integration
        from biweekly_bills.backups import BackupManager
        from biweekly_bills.production_readiness import inspect_production_readiness
        from biweekly_bills.sandbox_validation import run_sandbox_validation
        from biweekly_bills.ui.main_window import MainWindow
        from biweekly_bills.ui.pay_periods import PayPeriodsPage
        from biweekly_bills.ui.reconciliation_page import ReconciliationPage
        from biweekly_bills.ui.reports_page import ReportsPage
        from biweekly_bills.ui.settings_page import SettingsPage
        from biweekly_bills.ui.theme import APP_STYLESHEET

        self.assertTrue(callable(desktop_app.main))
        self.assertTrue(callable(install_desktop_integration))
        self.assertIsNotNone(MainWindow)
        self.assertIsNotNone(PayPeriodsPage)
        self.assertIsNotNone(ReconciliationPage)
        self.assertIsNotNone(ReportsPage)
        self.assertIsNotNone(SettingsPage)
        self.assertIsNotNone(BackupManager)
        self.assertTrue(callable(inspect_production_readiness))
        self.assertTrue(callable(run_sandbox_validation))
        self.assertIn("#c8ff3d", APP_STYLESHEET)
        self.assertIn("SegmentButton", APP_STYLESHEET)
        self.assertIn("WorkflowProgress", APP_STYLESHEET)


if __name__ == "__main__":
    unittest.main()
