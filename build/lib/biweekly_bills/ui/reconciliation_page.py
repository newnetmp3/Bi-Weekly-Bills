from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..auto_reconcile import AutoReconcileReport, reconcile_history
from ..backups import BackupManager
from ..database import Database
from ..reconciliation_audit import (
    AuditEntry,
    build_reconciliation_audit,
    run_integrity_diagnostics,
)
from ..settings import load_settings
from .busy_cursor import begin_busy_cursor, end_busy_cursor
from .context_menu import (
    begin_table_context_menu,
    copy_text,
    show_table_context_menu,
)
from .formatting import money
from .table_sort import (
    SortableTableWidgetItem,
    begin_table_refresh,
    configure_resizable_columns,
    end_table_refresh,
    set_sortable,
)


MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class _HistoryWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        database: Database,
        environment: str,
        backup_manager: BackupManager,
    ):
        super().__init__()
        self.database = database
        self.environment = environment
        self.backup_manager = backup_manager

    @Slot()
    def run(self) -> None:
        try:
            self.backup_manager.create_backup(
                "pre-history-reconcile"
            )
            self.finished.emit(
                reconcile_history(self.database, self.environment)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class _AuditWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database: Database, environment: str):
        super().__init__()
        self.database = database
        self.environment = environment

    @Slot()
    def run(self) -> None:
        try:
            entries = build_reconciliation_audit(
                self.database,
                self.environment,
            )
            diagnostics = run_integrity_diagnostics(
                self.database,
                self.environment,
                audit_entries=entries,
            )
            self.finished.emit(
                (self.environment, entries, diagnostics)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class ReconciliationPage(QWidget):
    def __init__(
        self,
        database: Database,
        on_data_changed: Callable[[], None],
        backup_manager: BackupManager,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.on_data_changed = on_data_changed
        self.backup_manager = backup_manager
        self._thread: QThread | None = None
        self._worker: _HistoryWorker | None = None
        self._audit_thread: QThread | None = None
        self._audit_worker: _AuditWorker | None = None
        self._audit_entries: list[AuditEntry] = []
        self._diagnostics = []
        self._audit_refresh_pending = False
        self._busy_cursor_active = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(14)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.verified_value = self._summary_card(cards, "Verified")
        self.review_value = self._summary_card(cards, "Needs review")
        self.unverified_value = self._summary_card(cards, "Paid · unverified")
        self.unpaid_value = self._summary_card(cards, "Unpaid")
        root.addLayout(cards)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search historical bills or reasons…")
        self.search.textChanged.connect(self._apply_cached_filter)
        toolbar.addWidget(self.search, 2)

        self.state_filter = QComboBox()
        self.state_filter.addItem("All historical states", "all")
        self.state_filter.addItem("Needs review", "Needs review")
        self.state_filter.addItem("Paid · unverified", "Paid · unverified")
        self.state_filter.addItem("Verified", "Verified")
        self.state_filter.addItem("Unpaid", "Unpaid")
        self.state_filter.currentIndexChanged.connect(
            self._apply_cached_filter
        )
        toolbar.addWidget(self.state_filter, 1)

        self.reconcile_button = QPushButton("Reconcile history")
        self.reconcile_button.setObjectName("PrimaryButton")
        self.reconcile_button.clicked.connect(self._start_history_reconcile)
        toolbar.addWidget(self.reconcile_button)

        self.refresh_button = QPushButton("Refresh local")
        self.refresh_button.setObjectName("SecondaryButton")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)

        root.addLayout(toolbar)

        self.status = QLabel(
            "Historical audit uses stored bank transactions only; it never initiates payments."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        audit_card = QFrame()
        audit_card.setObjectName("Card")
        audit_layout = QVBoxLayout(audit_card)
        audit_layout.setContentsMargins(16, 16, 16, 16)
        audit_layout.setSpacing(10)

        audit_header = QHBoxLayout()
        title = QLabel("Historical reconciliation audit")
        title.setObjectName("SectionTitle")
        self.audit_summary = QLabel("")
        self.audit_summary.setObjectName("Muted")
        audit_header.addWidget(title)
        audit_header.addStretch(1)
        audit_header.addWidget(self.audit_summary)
        audit_layout.addLayout(audit_header)

        self.audit_table = QTableWidget(0, 8)
        self.audit_table.setHorizontalHeaderLabels(
            [
                "Period",
                "Bill",
                "Cycle",
                "Due",
                "Paid",
                "State",
                "Closest bank evidence",
                "Reason",
            ]
        )
        self.audit_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.audit_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.audit_table.setAlternatingRowColors(True)
        self.audit_table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.audit_table,
            (130, 230, 90, 110, 110, 140, 340, 440),
        )
        set_sortable(self.audit_table)
        self.audit_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.audit_table.customContextMenuRequested.connect(
            self._show_audit_context_menu
        )
        audit_layout.addWidget(self.audit_table)
        root.addWidget(audit_card, 3)

        diagnostic_card = QFrame()
        diagnostic_card.setObjectName("Card")
        diagnostic_layout = QVBoxLayout(diagnostic_card)
        diagnostic_layout.setContentsMargins(16, 16, 16, 16)
        diagnostic_layout.setSpacing(10)

        diagnostic_header = QHBoxLayout()
        diagnostic_title = QLabel("Integrity diagnostics")
        diagnostic_title.setObjectName("SectionTitle")
        self.diagnostic_summary = QLabel("")
        self.diagnostic_summary.setObjectName("Muted")
        diagnostic_header.addWidget(diagnostic_title)
        diagnostic_header.addStretch(1)
        diagnostic_header.addWidget(self.diagnostic_summary)
        diagnostic_layout.addLayout(diagnostic_header)

        self.diagnostic_table = QTableWidget(0, 5)
        self.diagnostic_table.setHorizontalHeaderLabels(
            ["Severity", "Period", "Subject", "Check", "Detail"]
        )
        self.diagnostic_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.diagnostic_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.diagnostic_table.setAlternatingRowColors(True)
        self.diagnostic_table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.diagnostic_table,
            (100, 125, 240, 190, 520),
        )
        self.diagnostic_table.setMinimumHeight(190)
        set_sortable(self.diagnostic_table)
        self.diagnostic_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.diagnostic_table.customContextMenuRequested.connect(
            self._show_diagnostic_context_menu
        )
        diagnostic_layout.addWidget(self.diagnostic_table)
        root.addWidget(diagnostic_card, 2)

        if auto_refresh:
            self.refresh()

    def _show_audit_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.audit_table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, _column = context

        bill_item = self.audit_table.item(row, 1)
        state_item = self.audit_table.item(row, 5)
        reason_item = self.audit_table.item(row, 7)

        if bill_item is not None:
            search_bill = menu.addAction("Search this bill")
            search_bill.triggered.connect(
                lambda checked=False, value=bill_item.text():
                    self.search.setText(value)
            )

        if state_item is not None:
            state_index = self.state_filter.findData(
                state_item.text()
            )
            if state_index >= 0:
                filter_state = menu.addAction(
                    f"Show only {state_item.text()}"
                )
                filter_state.triggered.connect(
                    lambda checked=False, index=state_index:
                        self.state_filter.setCurrentIndex(index)
                )

        if reason_item is not None:
            copy_reason = menu.addAction("Copy audit reason")
            copy_reason.triggered.connect(
                lambda checked=False, value=reason_item.text():
                    copy_text(value)
            )

        menu.addSeparator()
        reconcile = menu.addAction("Reconcile history")
        reconcile.setEnabled(self.reconcile_button.isEnabled())
        reconcile.triggered.connect(self._start_history_reconcile)
        refresh = menu.addAction("Refresh audit")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(
            self.audit_table,
            menu,
            position,
        )

    def _show_diagnostic_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.diagnostic_table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, _column = context

        subject_item = self.diagnostic_table.item(row, 2)
        detail_item = self.diagnostic_table.item(row, 4)
        if subject_item is not None:
            search_subject = menu.addAction(
                "Search this subject in audit"
            )
            search_subject.triggered.connect(
                lambda checked=False, value=subject_item.text():
                    self.search.setText(value)
            )
        if detail_item is not None:
            copy_detail = menu.addAction(
                "Copy diagnostic detail"
            )
            copy_detail.triggered.connect(
                lambda checked=False, value=detail_item.text():
                    copy_text(value)
            )

        menu.addSeparator()
        refresh = menu.addAction("Refresh diagnostics")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(
            self.diagnostic_table,
            menu,
            position,
        )

    def _summary_card(self, layout: QHBoxLayout, label_text: str) -> QLabel:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel("0")
        value.setObjectName("MetricValue")
        box.addWidget(label)
        box.addWidget(value)
        layout.addWidget(card, 1)
        return value

    def _environment(self) -> str:
        return load_settings(require_keys=False).environment

    @staticmethod
    def _candidate_text(entry: AuditEntry) -> str:
        if not entry.candidate_transaction_id:
            return "—"
        parts: list[str] = []
        if entry.candidate_date:
            parts.append(entry.candidate_date)
        if entry.candidate_description:
            parts.append(entry.candidate_description)
        if entry.candidate_amount_cents is not None:
            parts.append(money(entry.candidate_amount_cents))
        if entry.candidate_account:
            parts.append(entry.candidate_account)
        return " · ".join(parts) if parts else entry.candidate_transaction_id

    def refresh(self) -> None:
        """Load the expensive historical audit off the GUI thread."""
        if (
            self._audit_thread is not None
            and self._audit_thread.isRunning()
        ):
            self._audit_refresh_pending = True
            return
        if self._thread is not None and self._thread.isRunning():
            self._audit_refresh_pending = True
            return

        environment = self._environment()
        self.status.setText(
            "Loading historical audit and integrity diagnostics…"
        )
        self.refresh_button.setEnabled(False)
        self.reconcile_button.setEnabled(False)

        thread = QThread(self)
        worker = _AuditWorker(self.database, environment)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._audit_finished)
        worker.failed.connect(self._audit_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._audit_thread_finished)
        self._audit_thread = thread
        self._audit_worker = worker
        thread.start()

    @Slot(object)
    def _audit_finished(self, result: object) -> None:
        if (
            not isinstance(result, tuple)
            or len(result) != 3
        ):
            self.status.setText(
                "Historical audit returned an unexpected result."
            )
            return

        environment, entries, diagnostics = result
        if str(environment) != self._environment():
            self._audit_refresh_pending = True
            return

        self._audit_entries = list(entries)
        self._diagnostics = list(diagnostics)
        self._apply_cached_filter()
        self.status.setText(
            "Historical audit loaded from stored bank transactions."
        )

    @Slot(str)
    def _audit_failed(self, message: str) -> None:
        self.status.setText(
            f"Historical audit failed: {message}"
        )

    @Slot()
    def _audit_thread_finished(self) -> None:
        self._audit_thread = None
        self._audit_worker = None
        history_running = (
            self._thread is not None
            and self._thread.isRunning()
        )
        self.refresh_button.setEnabled(not history_running)
        self.reconcile_button.setEnabled(not history_running)
        if self._audit_refresh_pending and not history_running:
            self._audit_refresh_pending = False
            self.refresh()

    def _apply_cached_filter(self, *_args) -> None:
        entries = self._audit_entries
        diagnostics = self._diagnostics

        counts = {
            "Verified": 0,
            "Needs review": 0,
            "Paid · unverified": 0,
            "Unpaid": 0,
        }
        for entry in entries:
            counts[entry.state] = counts.get(entry.state, 0) + 1

        self.verified_value.setText(str(counts["Verified"]))
        self.review_value.setText(str(counts["Needs review"]))
        self.unverified_value.setText(str(counts["Paid · unverified"]))
        self.unpaid_value.setText(str(counts["Unpaid"]))

        search = self.search.text().strip().casefold()
        wanted_state = self.state_filter.currentData()
        filtered: list[AuditEntry] = []
        for entry in entries:
            if wanted_state != "all" and entry.state != wanted_state:
                continue
            haystack = " ".join(
                (
                    entry.bill_name,
                    entry.state,
                    entry.reason,
                    entry.candidate_description or "",
                    entry.candidate_account or "",
                )
            ).casefold()
            if search and search not in haystack:
                continue
            filtered.append(entry)

        sorting = begin_table_refresh(self.audit_table)
        self.audit_table.setRowCount(len(filtered))
        for row_index, entry in enumerate(filtered):
            period = f"{MONTH_NAMES[entry.month - 1]} {entry.year}"
            candidate_text = self._candidate_text(entry)
            values = [
                (period, entry.year * 100 + entry.month),
                (entry.bill_name, entry.bill_name.casefold()),
                (entry.cycle, entry.cycle),
                (
                    money(entry.due_cents),
                    -1 if entry.due_cents is None else entry.due_cents,
                ),
                (
                    money(entry.paid_cents),
                    -1 if entry.paid_cents is None else entry.paid_cents,
                ),
                (entry.state, entry.state.casefold()),
                (candidate_text, candidate_text.casefold()),
                (entry.reason, entry.reason.casefold()),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                    user_data=entry.bill_instance_id,
                )
                if col in {3, 4}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                item.setToolTip(entry.reason)
                self.audit_table.setItem(row_index, col, item)
        end_table_refresh(self.audit_table, sorting)

        self.audit_summary.setText(
            f"{len(filtered)} shown · "
            f"{len(entries)} historical bill instance(s)"
        )

        sorting = begin_table_refresh(self.diagnostic_table)
        self.diagnostic_table.setRowCount(len(diagnostics))
        for row_index, issue in enumerate(diagnostics):
            values = [
                (issue.severity.upper(), issue.severity),
                (issue.period or "—", issue.period or ""),
                (issue.subject, issue.subject.casefold()),
                (issue.code.replace("-", " "), issue.code),
                (issue.detail, issue.detail.casefold()),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                )
                item.setToolTip(issue.detail)
                self.diagnostic_table.setItem(row_index, col, item)
        end_table_refresh(self.diagnostic_table, sorting)

        error_count = sum(
            1 for issue in diagnostics
            if issue.severity == "error"
        )
        warning_count = sum(
            1 for issue in diagnostics
            if issue.severity == "warning"
        )
        info_count = sum(
            1 for issue in diagnostics
            if issue.severity == "info"
        )
        self.diagnostic_summary.setText(
            f"{error_count} error · "
            f"{warning_count} warning · "
            f"{info_count} info"
        )

    def _start_history_reconcile(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        if (
            self._audit_thread is not None
            and self._audit_thread.isRunning()
        ):
            self.status.setText(
                "Wait for the current audit load to finish before "
                "reconciling history."
            )
            return

        environment = self._environment()
        self.reconcile_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText(
            "Re-scanning completed months against stored bank transactions…"
        )
        if not self._busy_cursor_active:
            begin_busy_cursor()
            self._busy_cursor_active = True

        thread = QThread(self)
        worker = _HistoryWorker(
            self.database,
            environment,
            self.backup_manager,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._history_finished)
        worker.failed.connect(self._history_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._history_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _history_finished(self, result: object) -> None:
        report = result
        if not isinstance(report, AutoReconcileReport):
            self.status.setText("Historical reconciliation returned no report.")
        else:
            self.status.setText(
                f"Historical reconciliation complete: "
                f"{report.matched_count} matched · "
                f"{report.internal_transfer_count} credit/loan pair(s) · "
                f"{report.review_count} left for review."
            )
        self.on_data_changed()
        self.refresh()

    @Slot(str)
    def _history_failed(self, message: str) -> None:
        self.status.setText(f"Historical reconciliation failed: {message}")

    @Slot()
    def _history_thread_finished(self) -> None:
        self.reconcile_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        if self._busy_cursor_active:
            end_busy_cursor()
            self._busy_cursor_active = False
        self._thread = None
        self._worker = None
        if self._audit_refresh_pending:
            self._audit_refresh_pending = False
            self.refresh()
