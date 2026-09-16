from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..database import Database
from ..reports import (
    ExportResult,
    FINANCIAL_REPORT_LABELS,
    MONTH_NAMES,
    OTHER_FINANCIAL_REPORTS,
    QUICK_FINANCIAL_REPORTS,
    build_report_bundle,
    default_reports_dir,
    export_financial_report,
    export_reports,
)
from .busy_cursor import begin_busy_cursor, end_busy_cursor
from .context_menu import (
    begin_table_context_menu,
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


class _ReportWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        database: Database,
        year: int,
        month: int,
        formats: tuple[str, ...],
        output_dir: Path,
        report_key: str | None = None,
    ):
        super().__init__()
        self.database = database
        self.year = year
        self.month = month
        self.formats = formats
        self.output_dir = output_dir
        self.report_key = report_key

    @Slot()
    def run(self) -> None:
        try:
            if self.report_key is not None:
                result = export_financial_report(
                    self.database,
                    self.year,
                    self.month,
                    self.report_key,
                    output_dir=self.output_dir,
                )
            else:
                result = export_reports(
                    self.database,
                    self.year,
                    self.month,
                    formats=self.formats,
                    output_dir=self.output_dir,
                )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class ReportsPage(QWidget):
    def __init__(
        self,
        database: Database,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.output_dir = default_reports_dir(database)
        self._thread: QThread | None = None
        self._worker: _ReportWorker | None = None
        self._busy_cursor_active = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(QLabel("Month"))
        self.month = QComboBox()
        self.month.addItems(MONTH_NAMES)
        self.month.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.month)

        toolbar.addWidget(QLabel("Year"))
        self.year = QComboBox()
        self.year.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.year)

        toolbar.addStretch(1)

        self.xlsx_button = QPushButton("Export Excel")
        self.xlsx_button.setObjectName("SecondaryButton")
        self.xlsx_button.clicked.connect(lambda: self._start_export(("xlsx",)))
        toolbar.addWidget(self.xlsx_button)

        self.pdf_button = QPushButton("Export PDF")
        self.pdf_button.setObjectName("SecondaryButton")
        self.pdf_button.clicked.connect(lambda: self._start_export(("pdf",)))
        toolbar.addWidget(self.pdf_button)

        self.ods_button = QPushButton("Export ODS")
        self.ods_button.setObjectName("SecondaryButton")
        self.ods_button.clicked.connect(lambda: self._start_export(("ods",)))
        toolbar.addWidget(self.ods_button)

        self.all_button = QPushButton("Export all")
        self.all_button.setObjectName("PrimaryButton")
        self.all_button.clicked.connect(
            lambda: self._start_export(("xlsx", "pdf", "ods"))
        )
        toolbar.addWidget(self.all_button)

        root.addLayout(toolbar)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.scheduled_card, self.scheduled_value, self.scheduled_detail = self._summary_card(
            "Scheduled"
        )
        self.paid_card, self.paid_value, self.paid_detail = self._summary_card("Paid")
        self.remaining_card, self.remaining_value, self.remaining_detail = self._summary_card(
            "Remaining"
        )
        self.bank_card, self.bank_value, self.bank_detail = self._summary_card("Bank data")
        cards.addWidget(self.scheduled_card)
        cards.addWidget(self.paid_card)
        cards.addWidget(self.remaining_card)
        cards.addWidget(self.bank_card)
        root.addLayout(cards)

        financial_card = QFrame()
        financial_card.setObjectName("Card")
        financial_layout = QVBoxLayout(financial_card)
        financial_layout.setContentsMargins(16, 14, 16, 14)
        financial_layout.setSpacing(10)

        financial_header = QHBoxLayout()
        financial_title = QLabel("Financial reports")
        financial_title.setObjectName("SectionTitle")
        financial_help = QLabel(
            "Quick reports create focused Excel workbooks for the selected period."
        )
        financial_help.setObjectName("Muted")
        financial_header.addWidget(financial_title)
        financial_header.addStretch(1)
        financial_header.addWidget(financial_help)
        financial_layout.addLayout(financial_header)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        self.quick_report_buttons: dict[str, QPushButton] = {}
        for report_key, label in QUICK_FINANCIAL_REPORTS:
            button = QPushButton(label)
            button.setObjectName("SecondaryButton")
            button.clicked.connect(
                lambda checked=False, key=report_key:
                    self._start_financial_report(key)
            )
            self.quick_report_buttons[report_key] = button
            quick_row.addWidget(button)

        quick_row.addStretch(1)
        quick_row.addWidget(QLabel("Other Reports"))
        self.other_reports = QComboBox()
        for report_key, label in OTHER_FINANCIAL_REPORTS:
            self.other_reports.addItem(label, report_key)
        quick_row.addWidget(self.other_reports)

        self.run_other_report_button = QPushButton("Run report")
        self.run_other_report_button.setObjectName("SecondaryButton")
        self.run_other_report_button.clicked.connect(
            self._run_selected_other_report
        )
        quick_row.addWidget(self.run_other_report_button)
        financial_layout.addLayout(quick_row)
        root.addWidget(financial_card)

        report_card = QFrame()
        report_card.setObjectName("Card")
        report_layout = QVBoxLayout(report_card)
        report_layout.setContentsMargins(16, 16, 16, 16)
        report_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Report preview")
        title.setObjectName("SectionTitle")
        self.preview_detail = QLabel("")
        self.preview_detail.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.preview_detail)
        report_layout.addLayout(header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Cycle", "Bill", "Due", "Paid", "Remaining", "Method", "Status"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.table,
            (90, 280, 110, 110, 125, 170, 135),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        report_layout.addWidget(self.table)
        root.addWidget(report_card, 1)

        output_card = QFrame()
        output_card.setObjectName("Card")
        output_layout = QHBoxLayout(output_card)
        output_layout.setContentsMargins(16, 12, 16, 12)
        path_label = QLabel("Report folder")
        path_label.setObjectName("MetricLabel")
        output_layout.addWidget(path_label)
        self.output_path = QLabel(str(self.output_dir))
        self.output_path.setObjectName("Muted")
        self.output_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        output_layout.addWidget(self.output_path, 1)

        open_button = QPushButton("Open report folder")
        open_button.setObjectName("SecondaryButton")
        open_button.clicked.connect(self._open_output_dir)
        output_layout.addWidget(open_button)

        root.addWidget(output_card)

        self.status = QLabel(
            "Reports are read-only exports. Excel and ODS include Summary, Bills, Funding, "
            "Transactions, and Accounts sheets."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self._load_years()
        today = date.today()
        self.year.blockSignals(True)
        self.month.blockSignals(True)
        if self.year.findText(str(today.year)) >= 0:
            self.year.setCurrentText(str(today.year))
        self.month.setCurrentIndex(today.month - 1)
        self.year.blockSignals(False)
        self.month.blockSignals(False)
        if auto_refresh:
            self.refresh()

    def _show_table_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, _row, _column = context

        menu.addSeparator()
        excel = menu.addAction("Export Excel")
        excel.setEnabled(self.xlsx_button.isEnabled())
        excel.triggered.connect(
            lambda checked=False:
                self._start_export(("xlsx",))
        )
        pdf = menu.addAction("Export PDF")
        pdf.setEnabled(self.pdf_button.isEnabled())
        pdf.triggered.connect(
            lambda checked=False:
                self._start_export(("pdf",))
        )
        ods = menu.addAction("Export ODS")
        ods.setEnabled(self.ods_button.isEnabled())
        ods.triggered.connect(
            lambda checked=False:
                self._start_export(("ods",))
        )
        export_all = menu.addAction("Export all formats")
        export_all.setEnabled(self.all_button.isEnabled())
        export_all.triggered.connect(
            lambda checked=False:
                self._start_export(("xlsx", "pdf", "ods"))
        )

        menu.addSeparator()
        open_folder = menu.addAction("Open report folder")
        open_folder.triggered.connect(self._open_output_dir)
        refresh = menu.addAction("Refresh report preview")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.table, menu, position)

    def _summary_card(self, label_text: str):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
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

    def _load_years(self) -> None:
        current = self.year.currentText()
        years = self.database.available_years() or [date.today().year]
        self.year.blockSignals(True)
        self.year.clear()
        self.year.addItems([str(year) for year in years])
        if current and self.year.findText(current) >= 0:
            self.year.setCurrentText(current)
        self.year.blockSignals(False)

    def _selected_period(self) -> tuple[int, int]:
        year = int(self.year.currentText()) if self.year.currentText() else date.today().year
        month = self.month.currentIndex() + 1
        return year, month

    def refresh(self) -> None:
        if not self.year.currentText():
            return

        year, month = self._selected_period()
        bundle = build_report_bundle(self.database, year, month)

        self.scheduled_value.setText(money(bundle.month_summary.due_cents))
        self.scheduled_detail.setText(
            f"1st {money(bundle.first_summary.due_cents)} · "
            f"15th {money(bundle.fifteenth_summary.due_cents)}"
        )
        self.paid_value.setText(money(bundle.month_summary.paid_cents))
        self.paid_detail.setText(
            f"{bundle.month_summary.paid_count} of {bundle.month_summary.bill_count} "
            "bill rows have recorded payments"
        )
        self.remaining_value.setText(money(bundle.month_summary.remaining_cents))
        if bundle.funding.environment is None:
            self.remaining_detail.setText(
                "Bills funding assignments are not ready yet."
            )
        else:
            self.remaining_detail.setText(
                f"Planned Bills transfer: {money(bundle.funding.total_transfer_cents)}"
            )

        self.bank_value.setText(bundle.bank_label)
        self.bank_detail.setText(
            f"{len(bundle.transactions)} transaction(s) · "
            f"{bundle.matched_count} matched · {bundle.unresolved_count} unresolved"
        )

        self.preview_detail.setText(
            f"{bundle.month_label} · {len(bundle.bills)} bill rows · "
            "PDF + Excel + ODS"
        )

        sorting = begin_table_refresh(self.table)
        self.table.setRowCount(len(bundle.bills))
        for row_index, row in enumerate(bundle.bills):
            values = [
                (row.cycle, row.cycle),
                (row.name, row.name.casefold()),
                (money(row.due_cents), row.due_cents),
                (money(row.paid_cents), row.paid_cents),
                (money(row.remaining_cents), row.remaining_cents),
                (row.method or "—", (row.method or "").casefold()),
                (row.status or "—", (row.status or "").casefold()),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                )
                if col in {2, 3, 4}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row_index, col, item)
        end_table_refresh(self.table, sorting)

    def _set_export_enabled(self, enabled: bool) -> None:
        for button in (
            self.xlsx_button,
            self.pdf_button,
            self.ods_button,
            self.all_button,
            *self.quick_report_buttons.values(),
            self.run_other_report_button,
        ):
            button.setEnabled(enabled)
        self.other_reports.setEnabled(enabled)

    def _start_export(self, formats: tuple[str, ...]) -> None:
        label = ", ".join(fmt.upper() for fmt in formats)
        self._launch_report_worker(
            formats=formats,
            report_key=None,
            status_text=f"Generating {label} report…",
        )

    def _start_financial_report(self, report_key: str) -> None:
        label = FINANCIAL_REPORT_LABELS.get(report_key, report_key)
        self._launch_report_worker(
            formats=(),
            report_key=report_key,
            status_text=f"Generating {label} Excel report…",
        )

    def _run_selected_other_report(self) -> None:
        report_key = self.other_reports.currentData()
        if report_key:
            self._start_financial_report(str(report_key))

    def _launch_report_worker(
        self,
        *,
        formats: tuple[str, ...],
        report_key: str | None,
        status_text: str,
    ) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        year, month = self._selected_period()
        self._set_export_enabled(False)
        self.status.setText(status_text)

        if not self._busy_cursor_active:
            begin_busy_cursor()
            self._busy_cursor_active = True

        thread = QThread(self)
        worker = _ReportWorker(
            self.database,
            year,
            month,
            formats,
            self.output_dir,
            report_key=report_key,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._export_finished)
        worker.failed.connect(self._export_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _export_finished(self, result: Any) -> None:
        if not isinstance(result, ExportResult):
            self.status.setText("Report export returned an unexpected result.")
            return

        names = ", ".join(path.name for path in result.paths)
        self.status.setText(
            f"Export complete: {names} · {self.output_dir}"
        )

    @Slot(str)
    def _export_failed(self, message: str) -> None:
        self.status.setText(f"Report export failed: {message}")

    @Slot()
    def _thread_finished(self) -> None:
        self._set_export_enabled(True)
        if self._busy_cursor_active:
            end_busy_cursor()
            self._busy_cursor_active = False
        self._thread = None
        self._worker = None

    def _open_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))
