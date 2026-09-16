import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHeaderView,
    QStyleOptionViewItem,
)

from biweekly_bills import settings
from biweekly_bills.backups import BackupManager
from biweekly_bills.bank_connection import ConnectionState
from biweekly_bills.database import Database
from biweekly_bills.ui.main_window import MainWindow


def state(action: str) -> ConnectionState:
    return ConnectionState(
        action=action,
        headline="Test connection state",
        detail="Test detail",
        primary_label="Continue",
        can_repair=action == "sync",
    )


class FirstRunUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, action: str):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        backups = BackupManager(
            db,
            backup_dir=Path(temp.name) / "backups",
        )

        connection = state(action)
        patches = (
            patch(
                "biweekly_bills.ui.main_window.connection_state",
                return_value=connection,
            ),
            patch(
                "biweekly_bills.ui.settings_page.connection_state",
                return_value=connection,
            ),
            patch.object(
                settings,
                "USER_CONFIG_DIR",
                Path(temp.name) / "config",
            ),
        )
        for active_patch in patches:
            active_patch.start()
        for active_patch in reversed(patches):
            self.addCleanup(active_patch.stop)

        window = MainWindow(db, backups)
        window.resize(1400, 900)
        window.show()
        self.app.processEvents()
        self.addCleanup(window.close)
        return window

    def test_unconfigured_first_run_opens_settings(self):
        window = self._window("configure")

        self.assertIs(window.pages.currentWidget(), window.settings)
        self.assertEqual(window.page_title.text(), "Settings")
        labels = [
            window.nav_list.item(row).text()
            for row in range(window.nav_list.count())
        ]
        self.assertEqual(
            labels,
            [
                "Overview",
                "Bills",
                "Pay Periods",
                "Transactions",
                "Reconciliation",
                "Reports",
                "Settings",
            ],
        )
        self.assertNotIn("Accounts", labels)
        self.assertTrue(window.settings.accounts_panel.embedded)
        self.assertFalse(
            window.settings.accounts_panel.sync_button.isVisible()
        )
        self.assertEqual(
            window.settings.accounts_panel.table.columnCount(),
            9,
        )

        # Only the visible Settings page should load data at startup.
        self.assertEqual(window.dashboard.table.rowCount(), 0)
        self.assertEqual(window.bills.bill_table.rowCount(), 0)
        self.assertEqual(window.pay_periods.table.rowCount(), 0)
        self.assertEqual(window.transactions._rows, [])
        self.assertIsNone(window.reconciliation._audit_thread)
        self.assertEqual(window.reconciliation.audit_table.rowCount(), 0)
        self.assertEqual(window.reports.table.rowCount(), 0)

    def test_navigation_reuses_clean_page_until_data_is_marked_dirty(self):
        window = self._window("sync")

        with patch.object(window.transactions, "refresh") as refresh:
            window._navigate(
                3,
                "Transactions",
                "Bank activity, matches, and review",
            )
            self.assertEqual(refresh.call_count, 1)

            window._navigate(
                0,
                "Overview",
                "Your 1st/15th bill workflow at a glance",
            )
            window._navigate(
                3,
                "Transactions",
                "Bank activity, matches, and review",
            )
            self.assertEqual(refresh.call_count, 1)

            window._navigate(
                0,
                "Overview",
                "Your 1st/15th bill workflow at a glance",
            )
            window._refresh_bank_pages()
            window._navigate(
                3,
                "Transactions",
                "Bank activity, matches, and review",
            )
            self.assertEqual(refresh.call_count, 2)

    def test_sidebar_names_and_order_are_editable_and_autosaved(self):
        window = self._window("sync")

        pay_item = window._nav_item_for_page(2)
        self.assertIsNotNone(pay_item)
        self.assertFalse(window.nav_edit_button.isChecked())
        self.assertEqual(window.nav_list.toolTip(), "")
        self.assertEqual(pay_item.toolTip(), "")
        self.assertFalse(window.nav_hint.isVisible())
        self.assertEqual(
            window.nav_list.editTriggers(),
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        self.assertFalse(
            bool(pay_item.flags() & Qt.ItemFlag.ItemIsEditable)
        )

        window.nav_edit_button.setChecked(True)
        self.app.processEvents()
        self.assertTrue(window.nav_hint.isVisible())
        self.assertNotEqual(window.nav_list.toolTip(), "")
        self.assertNotEqual(pay_item.toolTip(), "")
        self.assertTrue(
            bool(pay_item.flags() & Qt.ItemFlag.ItemIsEditable)
        )
        self.assertTrue(
            bool(pay_item.flags() & Qt.ItemFlag.ItemIsDragEnabled)
        )
        self.assertFalse(
            bool(pay_item.flags() & Qt.ItemFlag.ItemIsDropEnabled)
        )
        self.assertEqual(
            window.nav_list.dragDropMode(),
            QAbstractItemView.DragDropMode.InternalMove,
        )

        rect = window.nav_list.visualItemRect(pay_item)
        self.assertFalse(
            window.nav_list.is_drag_handle_position(
                QPoint(4, rect.center().y())
            )
        )
        self.assertTrue(
            window.nav_list.is_drag_handle_position(
                QPoint(
                    window.nav_list.viewport().width() - 4,
                    rect.center().y(),
                )
            )
        )

        pay_item.setText("Pay Bills Now")
        self.app.processEvents()

        preferences = settings.load_user_ui_preferences()
        self.assertEqual(
            preferences["sidebar_labels"]["pay_periods"],
            "Pay Bills Now",
        )

        pay_row = window.nav_list.row(pay_item)
        active_item = window._nav_item_for_page(0)
        self.assertIsNotNone(active_item)
        window.nav_list.setCurrentItem(active_item)
        self.assertTrue(
            window.nav_list.move_item_to_row(
                pay_row,
                0,
                preserve_item=active_item,
            )
        )
        self.assertIs(window.nav_list.currentItem(), active_item)

        preferences = settings.load_user_ui_preferences()
        self.assertEqual(
            preferences["sidebar_order"][0],
            "pay_periods",
        )

        window._navigate(
            2,
            "Pay Periods",
            "Pay bills and edit each period inline",
        )
        self.assertEqual(window.page_title.text(), "Pay Bills Now")
        self.assertIs(window.pages.currentWidget(), window.pay_periods)

        window.nav_edit_button.setChecked(False)
        self.app.processEvents()
        self.assertEqual(window.nav_list.toolTip(), "")
        self.assertEqual(pay_item.toolTip(), "")
        self.assertFalse(window.nav_hint.isVisible())
        self.assertFalse(
            window.nav_list.is_drag_handle_position(
                QPoint(
                    window.nav_list.viewport().width() - 4,
                    rect.center().y(),
                )
            )
        )

    def test_sidebar_reordering_is_insert_only_and_never_loses_rows(self):
        window = self._window("sync")
        window.nav_edit_button.setChecked(True)
        self.app.processEvents()

        original_keys = [
            str(
                window.nav_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                )
            )
            for row in range(window.nav_list.count())
        ]
        original_count = window.nav_list.count()

        # Dropping on the upper/lower half of a row always resolves to an
        # insertion boundary rather than making that row a drop target.
        target_item = window.nav_list.item(1)
        target_rect = window.nav_list.visualItemRect(target_item)
        upper = QPoint(
            target_rect.center().x(),
            target_rect.top() + 1,
        )
        lower = QPoint(
            target_rect.center().x(),
            target_rect.bottom() - 1,
        )
        self.assertEqual(
            window.nav_list.insertion_row_for_position(upper),
            1,
        )
        self.assertEqual(
            window.nav_list.insertion_row_for_position(lower),
            2,
        )

        # Move the last row over the top half of the second row.
        self.assertTrue(
            window.nav_list.move_item_to_row(
                original_count - 1,
                window.nav_list.insertion_row_for_position(upper),
            )
        )
        self.assertEqual(window.nav_list.count(), original_count)
        after_keys = [
            str(
                window.nav_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                )
            )
            for row in range(window.nav_list.count())
        ]
        self.assertCountEqual(after_keys, original_keys)
        self.assertEqual(len(set(after_keys)), original_count)

        # Top, bottom, adjacent, and self/no-op moves all retain every row.
        self.assertTrue(
            window.nav_list.move_item_to_row(
                window.nav_list.count() - 1,
                0,
            )
        )
        self.assertTrue(
            window.nav_list.move_item_to_row(
                0,
                window.nav_list.count(),
            )
        )
        self.assertFalse(
            window.nav_list.move_item_to_row(2, 2)
        )
        final_keys = [
            str(
                window.nav_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                )
            )
            for row in range(window.nav_list.count())
        ]
        self.assertEqual(window.nav_list.count(), original_count)
        self.assertCountEqual(final_keys, original_keys)
        self.assertEqual(len(set(final_keys)), original_count)

    def test_sidebar_grip_does_not_navigate_while_reordering(self):
        window = self._window("sync")
        window.nav_edit_button.setChecked(True)
        self.app.processEvents()

        active_item = window._nav_item_for_page(0)
        pay_item = window._nav_item_for_page(2)
        self.assertIsNotNone(active_item)
        self.assertIsNotNone(pay_item)
        window.nav_list.setCurrentItem(active_item)
        self.assertIs(window.pages.currentWidget(), window.dashboard)

        source_row = window.nav_list.row(pay_item)

        # Match the real grip-drag state: the list temporarily makes the
        # dragged item current, but that must never navigate the app.
        window.nav_list.blockSignals(True)
        window.nav_list.setCurrentItem(pay_item)
        window.nav_list.blockSignals(False)

        self.assertTrue(
            window.nav_list.move_item_to_row(
                source_row,
                0,
                preserve_item=active_item,
            )
        )
        self.assertIs(window.nav_list.currentItem(), active_item)
        self.assertIs(window.pages.currentWidget(), window.dashboard)
        self.assertEqual(window.page_title.text(), active_item.text())

    def test_sidebar_rename_editor_stays_inside_text_area(self):
        window = self._window("sync")
        window.nav_edit_button.setChecked(True)
        self.app.processEvents()

        nav = window.nav_list
        item = window._nav_item_for_page(2)
        self.assertIsNotNone(item)
        index = nav.indexFromItem(item)
        row_rect = nav.visualItemRect(item)

        option = QStyleOptionViewItem()
        option.rect = row_rect
        delegate = nav.itemDelegate()
        editor = delegate.createEditor(
            nav.viewport(),
            option,
            index,
        )
        self.addCleanup(editor.deleteLater)
        delegate.updateEditorGeometry(
            editor,
            option,
            index,
        )

        editor_rect = editor.geometry()
        self.assertEqual(
            editor.objectName(),
            "SidebarNavEditor",
        )
        self.assertGreaterEqual(
            editor_rect.left(),
            row_rect.left() + delegate.EDITOR_LEFT_INSET,
        )
        self.assertLessEqual(
            editor_rect.right(),
            row_rect.right()
            - nav.HANDLE_WIDTH
            - delegate.EDITOR_RIGHT_GAP,
        )
        self.assertGreaterEqual(
            row_rect.height(),
            delegate.EDITOR_MIN_HEIGHT
            + delegate.EDITOR_VERTICAL_MARGIN * 2,
        )
        self.assertGreaterEqual(
            editor_rect.height(),
            delegate.EDITOR_MIN_HEIGHT,
        )
        self.assertLessEqual(
            editor_rect.height(),
            delegate.EDITOR_MAX_HEIGHT,
        )
        self.assertGreaterEqual(
            editor_rect.height(),
            editor.fontMetrics().height() + 8,
        )
        self.assertGreater(editor_rect.width(), 48)
        self.assertLessEqual(
            editor_rect.bottom(),
            row_rect.bottom(),
        )
        self.assertGreaterEqual(
            editor_rect.top(),
            row_rect.top(),
        )

    def test_sidebar_start_drag_never_delegates_item_ownership_to_qt(self):
        window = self._window("sync")
        window.nav_edit_button.setChecked(True)
        self.app.processEvents()

        nav = window.nav_list
        active_item = window._nav_item_for_page(0)
        pay_item = window._nav_item_for_page(2)
        self.assertIsNotNone(active_item)
        self.assertIsNotNone(pay_item)

        original_keys = [
            str(
                nav.item(row).data(Qt.ItemDataRole.UserRole)
            )
            for row in range(nav.count())
        ]
        nav.setCurrentItem(active_item)

        nav._drag_from_handle = True
        nav._drag_source_row = nav.row(pay_item)
        nav._selection_before_drag = active_item

        fake_drag = MagicMock()
        fake_drag.exec.return_value = Qt.DropAction.IgnoreAction
        with patch(
            "biweekly_bills.ui.main_window.QDrag",
            return_value=fake_drag,
        ):
            nav.startDrag(Qt.DropAction.MoveAction)

        fake_drag.setMimeData.assert_called_once()
        fake_drag.exec.assert_called_once_with(
            Qt.DropAction.MoveAction,
            Qt.DropAction.MoveAction,
        )

        after_keys = [
            str(
                nav.item(row).data(Qt.ItemDataRole.UserRole)
            )
            for row in range(nav.count())
        ]
        self.assertEqual(after_keys, original_keys)
        self.assertEqual(nav.count(), len(original_keys))
        self.assertIs(nav.currentItem(), active_item)
        self.assertFalse(nav._drag_from_handle)
        self.assertEqual(nav._drag_source_row, -1)
        self.assertIsNone(nav._selection_before_drag)
        self.assertIs(window.pages.currentWidget(), window.dashboard)

    def test_settings_transfer_source_action_saves_and_shows_result(self):
        window = self._window("sync")
        panel = window.settings.accounts_panel
        db = panel.database

        db.upsert_bank_account(
            environment="production",
            plaid_account_id="acct-bills",
            name="Bills Checking",
            mask="1111",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=90000,
            last_synced_at="2026-09-16T12:00:00+00:00",
        )
        db.upsert_bank_account(
            environment="production",
            plaid_account_id="acct-source",
            name="Primary Checking",
            mask="2222",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=200000,
            available_balance_cents=190000,
            last_synced_at="2026-09-16T12:00:00+00:00",
        )

        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE bank_accounts
                SET is_bills_checking=1
                WHERE environment='production'
                  AND plaid_account_id='acct-bills'
                """
            )

        fake_settings = SimpleNamespace(environment="production")
        fake_connection = {
            "credential_present": True,
            "keys_configured": True,
        }
        panel.on_data_changed = MagicMock()

        with (
            patch(
                "biweekly_bills.ui.bank_pages.load_settings",
                return_value=fake_settings,
            ),
            patch(
                "biweekly_bills.ui.bank_pages.connection_status",
                return_value=fake_connection,
            ),
            patch.object(
                panel.backup_manager,
                "create_backup",
            ),
        ):
            panel.refresh()

            source_row = next(
                row
                for row in range(panel.table.rowCount())
                if str(
                    panel.table.item(row, 0).data(
                        Qt.ItemDataRole.UserRole
                    )
                )
                == "acct-source"
            )
            panel.table.selectRow(source_row)
            panel.table.setCurrentCell(source_row, 0)
            self.app.processEvents()

            self.assertTrue(panel.source_button.isEnabled())
            self.assertEqual(
                panel.source_button.text(),
                "Use selected as Transfer Source",
            )

            panel.source_button.click()
            self.app.processEvents()

        saved = db.default_transfer_source_account("production")
        self.assertIsNotNone(saved)
        self.assertEqual(
            str(saved["plaid_account_id"]),
            "acct-source",
        )
        self.assertEqual(
            panel._selected_account_id(),
            "acct-source",
        )
        selected_row = panel.table.currentRow()
        self.assertGreaterEqual(selected_row, 0)
        self.assertEqual(
            panel.table.item(selected_row, 8).text(),
            "YES",
        )
        self.assertFalse(panel.source_button.isEnabled())
        self.assertEqual(
            panel.source_button.text(),
            "Selected is Transfer Source",
        )
        self.assertIn(
            "Transfer Source saved: Primary Checking ••••2222.",
            panel.status.text(),
        )
        panel.on_data_changed.assert_called_once()

    def test_all_primary_tables_have_user_resizable_columns(self):
        window = self._window("sync")

        tables = (
            window.dashboard.table,
            window.bills.bill_table,
            window.pay_periods.table,
            window.transactions.table,
            window.reconciliation.audit_table,
            window.reconciliation.diagnostic_table,
            window.reports.table,
            window.settings.accounts_panel.table,
            window.settings.table,
        )

        for table in tables:
            header = table.horizontalHeader()
            for column in range(table.columnCount()):
                self.assertEqual(
                    header.sectionResizeMode(column),
                    QHeaderView.ResizeMode.Interactive,
                    f"{table.objectName() or type(table).__name__} "
                    f"column {column} should be user-resizable",
                )
            self.assertGreaterEqual(
                header.minimumSectionSize(),
                64,
            )

        original = window.transactions.table.columnWidth(1)
        window.transactions.table.setColumnWidth(1, original + 137)
        self.assertEqual(
            window.transactions.table.columnWidth(1),
            original + 137,
        )

    def test_established_connection_opens_overview(self):
        window = self._window("sync")

        self.assertIs(window.pages.currentWidget(), window.dashboard)
        self.assertEqual(window.page_title.text(), "Overview")
        self.assertEqual(window.transactions._rows, [])
        self.assertIsNone(window.reconciliation._audit_thread)
        self.assertEqual(window.reports.table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
