from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..backups import BackupInfo, BackupManager
from ..bank_connection import (
    ConnectionResult,
    ConnectionState,
    connection_state,
    run_connection_action,
)
from ..database import Database
from ..secure_store import load_credentials
from ..settings import load_settings, save_user_bank_settings
from .bank_pages import AccountsPage
from .busy_cursor import begin_busy_cursor, end_busy_cursor
from .context_menu import (
    begin_table_context_menu,
    copy_text,
    show_table_context_menu,
)
from .table_sort import (
    SortableTableWidgetItem,
    begin_table_refresh,
    configure_resizable_columns,
    end_table_refresh,
    set_sortable,
)


def _size_text(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KiB"
    return f"{size_bytes / (1024 * 1024):.1f} MiB"


class _BackupWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        manager: BackupManager,
        *,
        restore_path: Path | None = None,
    ):
        super().__init__()
        self.manager = manager
        self.restore_path = restore_path

    @Slot()
    def run(self) -> None:
        try:
            if self.restore_path is None:
                result = ("backup", self.manager.create_backup("manual"))
            else:
                restored, safety = self.manager.restore(self.restore_path)
                result = ("restore", restored, safety)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class _ConnectionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database: Database, action: str):
        super().__init__()
        self.database = database
        self.action = action

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(
                run_connection_action(self.database, self.action)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class SettingsPage(QWidget):
    def __init__(
        self,
        database: Database,
        backup_manager: BackupManager,
        on_restored: Callable[[], None],
        on_data_changed: Callable[[], None],
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.backup_manager = backup_manager
        self.on_restored = on_restored
        self.on_data_changed = on_data_changed

        self._backup_thread: QThread | None = None
        self._backup_worker: _BackupWorker | None = None
        self._connection_thread: QThread | None = None
        self._connection_worker: _ConnectionWorker | None = None
        self._busy_cursor_active = False
        self._connection_state: ConnectionState | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 20, 28, 28)
        root.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # First-run / connection setup.
        connection_card = QFrame()
        connection_card.setObjectName("HeroCard")
        connection_layout = QVBoxLayout(connection_card)
        connection_layout.setContentsMargins(18, 16, 18, 16)
        connection_layout.setSpacing(10)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Bank connection")
        title.setObjectName("SectionTitle")
        subtitle = QLabel(
            "Connect once, then this app only reads balances and transactions."
        )
        subtitle.setObjectName("Muted")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)

        self.connection_value = QLabel("Checking…")
        self.connection_value.setObjectName("AccentValue")
        self.connection_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header.addWidget(self.connection_value)
        connection_layout.addLayout(header)

        self.connection_detail = QLabel("")
        self.connection_detail.setObjectName("Muted")
        self.connection_detail.setWordWrap(True)
        connection_layout.addWidget(self.connection_detail)

        checklist = QGridLayout()
        checklist.setHorizontalSpacing(14)
        checklist.setVerticalSpacing(4)
        self.api_step = QLabel("")
        self.link_step = QLabel("")
        self.sync_step = QLabel("")
        self.bills_step = QLabel("")
        self.source_step = QLabel("")
        for label in (
            self.api_step,
            self.link_step,
            self.sync_step,
            self.bills_step,
            self.source_step,
        ):
            label.setObjectName("Muted")
        checklist.addWidget(self.api_step, 0, 0)
        checklist.addWidget(self.link_step, 0, 1)
        checklist.addWidget(self.sync_step, 1, 0)
        checklist.addWidget(self.bills_step, 1, 1)
        checklist.addWidget(self.source_step, 2, 0, 1, 2)
        connection_layout.addLayout(checklist)

        credentials = QGridLayout()
        credentials.setHorizontalSpacing(10)
        credentials.setVerticalSpacing(6)

        credentials.addWidget(QLabel("Plaid Client ID"), 0, 0)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText(
            "Configured — leave blank to keep"
        )
        credentials.addWidget(self.client_id, 0, 1)

        credentials.addWidget(QLabel("Plaid Secret"), 0, 2)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText(
            "Configured — leave blank to keep"
        )
        credentials.addWidget(self.secret, 0, 3)

        credentials.addWidget(QLabel("Redirect URI (optional)"), 1, 0)
        self.redirect_uri = QLineEdit()
        self.redirect_uri.setPlaceholderText(
            "Leave blank unless your Plaid setup requires OAuth redirect"
        )
        credentials.addWidget(self.redirect_uri, 1, 1, 1, 3)
        connection_layout.addLayout(credentials)

        actions = QHBoxLayout()
        self.connection_button = QPushButton("Check setup")
        self.connection_button.setObjectName("PrimaryButton")
        self.connection_button.clicked.connect(
            self._primary_connection_action
        )
        actions.addWidget(self.connection_button)

        self.repair_button = QPushButton("Repair bank connection")
        self.repair_button.setObjectName("SecondaryButton")
        self.repair_button.clicked.connect(
            lambda checked=False: self._start_connection_worker("repair")
        )
        actions.addWidget(self.repair_button)

        self.connection_refresh = QPushButton("Refresh status")
        self.connection_refresh.setObjectName("SecondaryButton")
        self.connection_refresh.clicked.connect(self.refresh)
        actions.addWidget(self.connection_refresh)

        plaid_dashboard = QPushButton("Open Plaid dashboard")
        plaid_dashboard.setObjectName("SecondaryButton")
        plaid_dashboard.clicked.connect(
            lambda checked=False: QDesktopServices.openUrl(
                QUrl("https://dashboard.plaid.com/")
            )
        )
        actions.addWidget(plaid_dashboard)
        actions.addStretch(1)

        self.connection_status = QLabel("")
        self.connection_status.setObjectName("Muted")
        self.connection_status.setWordWrap(True)
        actions.addWidget(self.connection_status, 2)
        connection_layout.addLayout(actions)

        safety = QLabel(
            "The app will not create a replacement bank connection when one "
            "already exists. Repair and recovery reuse the existing connection."
        )
        safety.setObjectName("Muted")
        safety.setWordWrap(True)
        connection_layout.addWidget(safety)
        root.addWidget(connection_card)

        # Accounts live here instead of having a second navigation page.
        accounts_heading = QLabel("Accounts & bill funding")
        accounts_heading.setObjectName("SectionTitle")
        root.addWidget(accounts_heading)
        accounts_note = QLabel(
            "Sync balances/transactions, choose Bills Checking, and choose the "
            "Default Transfer Source. Bill-specific Payment Accounts are edited on Bills."
        )
        accounts_note.setObjectName("Muted")
        accounts_note.setWordWrap(True)
        root.addWidget(accounts_note)

        self.accounts_panel = AccountsPage(
            database,
            self._bank_data_changed,
            backup_manager,
            embedded=True,
            auto_refresh=False,
        )
        self.accounts_panel.setMinimumHeight(330)
        root.addWidget(self.accounts_panel)

        # Data safety is intentionally lower priority than getting started.
        safety_title = QLabel("Data safety & backups")
        safety_title.setObjectName("SectionTitle")
        root.addWidget(safety_title)

        summary = QHBoxLayout()
        summary.setSpacing(10)
        (
            self.last_card,
            self.last_value,
            self.last_detail,
        ) = self._summary_card("Last backup")
        (
            self.count_card,
            self.count_value,
            self.count_detail,
        ) = self._summary_card("Backups retained")
        (
            self.path_card,
            self.path_value,
            self.path_detail,
        ) = self._summary_card("Database")
        summary.addWidget(self.last_card)
        summary.addWidget(self.count_card)
        summary.addWidget(self.path_card)
        root.addLayout(summary)

        toolbar = QHBoxLayout()
        self.backup_button = QPushButton("Create backup now")
        self.backup_button.setObjectName("SecondaryButton")
        self.backup_button.clicked.connect(self._create_backup)
        toolbar.addWidget(self.backup_button)

        self.restore_button = QPushButton("Restore selected backup")
        self.restore_button.setObjectName("DangerButton")
        self.restore_button.clicked.connect(self._restore_selected)
        toolbar.addWidget(self.restore_button)

        refresh_backups = QPushButton("Refresh backups")
        refresh_backups.setObjectName("SecondaryButton")
        refresh_backups.clicked.connect(self._refresh_backups)
        toolbar.addWidget(refresh_backups)
        toolbar.addStretch(1)

        self.status = QLabel(
            "Application data is backed up locally. Bank credentials are stored separately."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        toolbar.addWidget(self.status, 2)
        root.addLayout(toolbar)

        backup_card = QFrame()
        backup_card.setObjectName("Card")
        card_layout = QVBoxLayout(backup_card)
        card_layout.setContentsMargins(14, 12, 14, 14)
        card_layout.setSpacing(8)

        backup_header = QHBoxLayout()
        backup_title = QLabel("Backup history")
        backup_title.setObjectName("SectionTitle")
        self.retention_label = QLabel("")
        self.retention_label.setObjectName("Muted")
        backup_header.addWidget(backup_title)
        backup_header.addStretch(1)
        backup_header.addWidget(self.retention_label)
        card_layout.addLayout(backup_header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Created", "Reason", "Size", "Integrity", "File"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.table,
            (175, 220, 100, 140, 420),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        self.table.setMinimumHeight(260)
        card_layout.addWidget(self.table)
        root.addWidget(backup_card)

        note = QLabel(
            "Restore is protected: the current database is backed up first, "
            "the selected snapshot is integrity-checked, and the restored "
            "database is verified again before the app resumes."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch(1)

        if auto_refresh:
            self.refresh()

    def _summary_card(self, label_text: str):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel("—")
        value.setObjectName("MetricValue")
        detail = QLabel("")
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        layout.addWidget(detail)
        return card, value, detail

    def _show_table_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, _column = context

        item = self.table.item(row, 0)
        raw_path = (
            ""
            if item is None
            else str(item.data(Qt.ItemDataRole.UserRole) or "")
        )
        if raw_path:
            copy_path = menu.addAction("Copy backup path")
            copy_path.triggered.connect(
                lambda checked=False, value=raw_path:
                    copy_text(value)
            )

        menu.addSeparator()
        restore = menu.addAction("Restore selected backup")
        restore.setEnabled(
            self.restore_button.isEnabled() and bool(raw_path)
        )
        restore.triggered.connect(self._restore_selected)

        backup = menu.addAction("Create backup now")
        backup.setEnabled(self.backup_button.isEnabled())
        backup.triggered.connect(self._create_backup)

        menu.addSeparator()
        refresh = menu.addAction("Refresh backup history")
        refresh.triggered.connect(self._refresh_backups)
        show_table_context_menu(self.table, menu, position)

    def refresh(self) -> None:
        self._refresh_connection()
        self.accounts_panel.refresh()
        self._refresh_backups()

    def _refresh_connection(self) -> None:
        state = connection_state(self.database)
        self._connection_state = state
        self.connection_value.setText(state.headline)
        self.connection_detail.setText(state.detail)
        self.connection_button.setText(
            "Save & continue"
            if state.action == "configure"
            else state.primary_label
        )
        self.repair_button.setVisible(state.can_repair)

        settings = load_settings(require_keys=False)
        if settings.environment == "production" and settings.client_id:
            self.client_id.setPlaceholderText(
                "Configured — leave blank to keep"
            )
        else:
            self.client_id.setPlaceholderText("Enter Plaid Client ID")
        if settings.environment == "production" and settings.secret:
            self.secret.setPlaceholderText(
                "Configured — leave blank to keep"
            )
        else:
            self.secret.setPlaceholderText("Enter bank API Secret")

        production = load_credentials("production")
        accounts = self.database.list_bank_accounts("production")
        bills_account = next(
            (
                row
                for row in accounts
                if int(row["is_bills_checking"] or 0)
            ),
            None,
        )
        transfer_source = self.database.default_transfer_source_account(
            "production"
        )
        sync = self.database.get_sync_state("production")

        self.api_step.setText(
            ("✓" if settings.client_id and settings.secret else "○")
            + " API credentials"
        )
        self.link_step.setText(
            ("✓" if production.get("access_token") else "○")
            + " Bank connected"
        )
        self.sync_step.setText(
            ("✓" if sync is not None and sync["last_sync_at"] else "○")
            + " Accounts synced"
        )
        self.bills_step.setText(
            ("✓" if bills_account is not None else "○")
            + " Bills Checking selected"
        )

        self.source_step.setText(
            ("✓" if transfer_source is not None else "○")
            + " Default Transfer Source selected"
        )

        if settings.redirect_uri:
            self.redirect_uri.setPlaceholderText(settings.redirect_uri)

    def _primary_connection_action(self) -> None:
        state = self._connection_state or connection_state(self.database)
        if state.action == "configure":
            self._save_and_continue()
        elif state.action in {"connect", "recover", "sync"}:
            self._start_connection_worker(state.action)
        else:
            self.refresh()

    def _save_and_continue(self) -> None:
        try:
            save_user_bank_settings(
                client_id=self.client_id.text().strip() or None,
                secret=self.secret.text().strip() or None,
                redirect_uri=(
                    self.redirect_uri.text().strip() or None
                ),
                environment="production",
            )
        except Exception as exc:
            self.connection_status.setText(
                f"Bank API settings were not saved: {exc}"
            )
            return

        self.client_id.clear()
        self.secret.clear()
        self.connection_status.setText(
            "Bank API settings saved securely for this user."
        )
        self._refresh_connection()

        state = self._connection_state
        if state is not None and state.action in {
            "connect",
            "recover",
            "sync",
        }:
            self._start_connection_worker(state.action)

    def _set_connection_controls_enabled(self, enabled: bool) -> None:
        self.connection_button.setEnabled(enabled)
        self.repair_button.setEnabled(enabled)
        self.connection_refresh.setEnabled(enabled)
        self.client_id.setEnabled(enabled)
        self.secret.setEnabled(enabled)
        self.redirect_uri.setEnabled(enabled)

    def _operation_in_progress(self) -> bool:
        connection_busy = (
            self._connection_thread is not None
            and self._connection_thread.isRunning()
        )
        backup_busy = (
            self._backup_thread is not None
            and self._backup_thread.isRunning()
        )
        return connection_busy or backup_busy

    def _start_connection_worker(self, action: str) -> None:
        if self._operation_in_progress():
            self.connection_status.setText(
                "Another Settings operation is already running."
            )
            return

        self._set_connection_controls_enabled(False)
        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        if action == "connect":
            message = (
                "Opening the secure bank connection in your browser…"
            )
        elif action == "repair":
            message = (
                "Opening the existing bank connection for repair…"
            )
        elif action == "recover":
            message = "Recovering the existing bank connection…"
        else:
            message = "Syncing accounts and transactions…"
        self.connection_status.setText(message)

        if not self._busy_cursor_active:
            begin_busy_cursor()
            self._busy_cursor_active = True

        thread = QThread(self)
        worker = _ConnectionWorker(self.database, action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._connection_finished)
        worker.failed.connect(self._connection_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._connection_thread_finished)
        self._connection_thread = thread
        self._connection_worker = worker
        thread.start()

    @Slot(object)
    def _connection_finished(self, result: object) -> None:
        if isinstance(result, ConnectionResult):
            self.connection_status.setText(result.message)
        else:
            self.connection_status.setText(
                "Bank connection operation completed."
            )
        self.accounts_panel.refresh()
        self._refresh_connection()
        self.on_data_changed()

    @Slot(str)
    def _connection_failed(self, message: str) -> None:
        self.connection_status.setText(
            f"Bank connection needs attention: {message}"
        )
        self._refresh_connection()

    @Slot()
    def _connection_thread_finished(self) -> None:
        self._set_connection_controls_enabled(True)
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        if self._busy_cursor_active:
            end_busy_cursor()
            self._busy_cursor_active = False
        self._connection_thread = None
        self._connection_worker = None

    def _bank_data_changed(self) -> None:
        self._refresh_connection()
        self.on_data_changed()

    def _refresh_backups(self) -> None:
        backups = self.backup_manager.list_backups(verify=False)
        latest = backups[0] if backups else None

        if latest is None:
            self.last_value.setText("Never")
            self.last_detail.setText("No verified backups yet.")
        else:
            self.last_value.setText(
                latest.created_at.astimezone().strftime("%b %d %H:%M")
            )
            self.last_detail.setText(latest.reason)

        self.count_value.setText(str(len(backups)))
        self.count_detail.setText(
            f"Rolling retention: newest {self.backup_manager.retention}"
        )
        self.path_value.setText(
            self.backup_manager.database.path.name
        )
        self.path_detail.setText(
            str(self.backup_manager.database.path)
        )
        self.retention_label.setText(
            f"Stored in {self.backup_manager.backup_dir}"
        )

        sorting = begin_table_refresh(self.table)
        self.table.setRowCount(len(backups))
        for row_index, info in enumerate(backups):
            created = info.created_at.astimezone().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            values = [
                (created, info.created_at.timestamp()),
                (info.reason, info.reason.casefold()),
                (
                    _size_text(info.size_bytes),
                    int(info.size_bytes),
                ),
                (
                    (
                        "OK"
                        if info.integrity_ok is True
                        else (
                            "FAILED"
                            if info.integrity_ok is False
                            else "Checked on restore"
                        )
                    ),
                    (
                        1
                        if info.integrity_ok is True
                        else (-1 if info.integrity_ok is False else 0)
                    ),
                ),
                (info.path.name, info.path.name.casefold()),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                    user_data=str(info.path),
                )
                self.table.setItem(row_index, col, item)
        end_table_refresh(self.table, sorting)

    def _selected_path(self) -> Path | None:
        items = self.table.selectedItems()
        if not items:
            return None
        raw = items[0].data(Qt.ItemDataRole.UserRole)
        return Path(str(raw)) if raw else None

    def _create_backup(self) -> None:
        self._start_backup_worker(None)

    def _restore_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            self.status.setText("Select a backup row first.")
            return
        self._start_backup_worker(path)

    def _start_backup_worker(
        self,
        restore_path: Path | None,
    ) -> None:
        if self._operation_in_progress():
            self.status.setText(
                "Another Settings operation is already running."
            )
            return

        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self._set_connection_controls_enabled(False)
        if restore_path is None:
            self.status.setText("Creating and verifying backup…")
        else:
            self.status.setText(
                "Creating a safety backup, restoring the selection, "
                "and verifying…"
            )

        if not self._busy_cursor_active:
            begin_busy_cursor()
            self._busy_cursor_active = True

        thread = QThread(self)
        worker = _BackupWorker(
            self.backup_manager,
            restore_path=restore_path,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._backup_finished)
        worker.failed.connect(self._backup_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._backup_thread_finished)
        self._backup_thread = thread
        self._backup_worker = worker
        thread.start()

    @Slot(object)
    def _backup_finished(self, result: Any) -> None:
        if not isinstance(result, tuple) or not result:
            self.status.setText(
                "Backup operation returned an unexpected result."
            )
            return

        if result[0] == "backup":
            info: BackupInfo = result[1]
            self.status.setText(
                f"Backup created and verified: {info.path.name}"
            )
        elif result[0] == "restore":
            restored: BackupInfo = result[1]
            safety: BackupInfo = result[2]
            self.status.setText(
                f"Restored {restored.path.name}. "
                f"Safety backup: {safety.path.name}"
            )
            self.on_restored()
        self._refresh_backups()

    @Slot(str)
    def _backup_failed(self, message: str) -> None:
        self.status.setText(
            f"Backup operation failed: {message}"
        )

    @Slot()
    def _backup_thread_finished(self) -> None:
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        self._set_connection_controls_enabled(True)
        if self._busy_cursor_active:
            end_busy_cursor()
            self._busy_cursor_active = False
        self._backup_thread = None
        self._backup_worker = None
