from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
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

from ..backups import BackupManager
from ..database import Database
from ..funding import build_funding_plan
from .adaptive_progress import AdaptiveTextProgressBar
from .context_menu import (
    begin_table_context_menu,
    copy_text,
    show_table_context_menu,
)
from .formatting import money, schedule_balance_phrase
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


def money_input(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"{cents / 100:.2f}"


def decimal_text(value: str | int | float | None) -> str:
    """Normalize workbook-style decimal artifacts for human editing."""
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    rounded = round(number, 2)
    if abs(rounded) < 0.005:
        rounded = 0.0
    return f"{rounded:.2f}"


def parse_money(text: str) -> int | None:
    raw = text.strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return int(round(float(raw) * 100))
    except ValueError as exc:
        raise ValueError("Amounts must be blank or numeric, for example 123.45.") from exc


def bank_status_text(
    *,
    manually_paid: bool,
    bank_verified: bool,
    year: int,
    month: int,
    today: date | None = None,
) -> str:
    if bank_verified:
        return "✓ Verified"
    if not manually_paid:
        return "—"

    current = today or date.today()
    if (year, month) < (current.year, current.month):
        return "Paid · unverified"
    return "Waiting for bank"


def verification_provenance_text(
    row,
    *,
    today: date | None = None,
) -> str:
    if int(row["bank_verified"] or 0):
        description = str(
            row["bank_verified_description"]
            or "posted bank transaction"
        )
        verified_date = str(row["bank_verified_date"] or "unknown date")
        account = str(
            row["bank_verified_account_name"]
            or "linked account"
        )
        mask = str(row["bank_verified_account_mask"] or "")
        if mask:
            account += f" ••••{mask}"

        evidence = row["bank_evidence_account_name"]
        if evidence:
            evidence_label = str(evidence)
            evidence_mask = str(row["bank_evidence_account_mask"] or "")
            if evidence_mask:
                evidence_label += f" ••••{evidence_mask}"
            return (
                f"Verified by {description} on {verified_date} from {account}; "
                f"paired receipt evidence is on {evidence_label}."
            )
        return (
            f"Verified by {description} on {verified_date} from {account}."
        )

    manually_paid = bool(int(row["manually_paid"] or 0))
    current = today or date.today()
    period = (int(row["year"]), int(row["month"]))
    if manually_paid and period < (current.year, current.month):
        return (
            "Paid checkpoint is recorded, but no posted bank transaction "
            "has been reconciled to this historical bill instance."
        )
    if manually_paid:
        return (
            "Paid checkpoint is recorded; waiting for a posted bank "
            "transaction to reconcile."
        )
    return "No bank reconciliation is recorded for this bill instance."


class PayPeriodsPage(QWidget):
    """Working bill-payment checklist backed directly by SQLite."""

    def __init__(
        self,
        database: Database,
        on_changed,
        backup_manager: BackupManager,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.on_changed = on_changed
        self.backup_manager = backup_manager
        self.selected_instance_id: int | None = None
        today = date.today()
        self.current_view = "15th" if today.day >= 15 else "1st"
        self._filling_table = False

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("PayPeriodsScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        page_layout.addWidget(self.scroll_area)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("PayPeriodsContent")
        self.scroll_area.setWidget(self.scroll_content)

        root = QVBoxLayout(self.scroll_content)
        self.content_layout = root
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(14)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        month_label = QLabel("Month")
        month_label.setObjectName("MetricLabel")
        toolbar.addWidget(month_label)

        self.month = QComboBox()
        self.month.addItems(MONTH_NAMES)
        self.month.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.month)

        year_label = QLabel("Year")
        year_label.setObjectName("MetricLabel")
        toolbar.addWidget(year_label)

        self.year = QComboBox()
        self.year.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.year)

        toolbar.addStretch(1)
        self.month_total = QLabel("")
        self.month_total.setObjectName("Pill")
        toolbar.addWidget(self.month_total)
        root.addLayout(toolbar)

        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        selector_label = QLabel("Work list")
        selector_label.setObjectName("MetricLabel")
        selector_row.addWidget(selector_label)

        self.period_group = QButtonGroup(self)
        self.period_group.setExclusive(True)
        self.period_buttons: dict[str, QPushButton] = {}
        for view, label in (
            ("1st", "1st Pay Period"),
            ("15th", "15th Pay Period"),
            ("month", "Whole Month"),
        ):
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, v=view: self.set_view(v))
            self.period_group.addButton(button)
            self.period_buttons[view] = button
            selector_row.addWidget(button)

        selector_row.addStretch(1)
        self.unfinished_only = QCheckBox("Show unfinished only")
        self.unfinished_only.stateChanged.connect(self.refresh)
        selector_row.addWidget(self.unfinished_only)
        root.addLayout(selector_row)

        self.workflow_card = QFrame()
        self.workflow_card.setObjectName("Card")
        workflow_layout = QHBoxLayout(self.workflow_card)
        workflow_layout.setContentsMargins(16, 12, 16, 12)
        workflow_layout.setSpacing(14)

        workflow_text = QVBoxLayout()
        self.workflow_title = QLabel("")
        self.workflow_title.setObjectName("SectionTitle")
        self.workflow_summary = QLabel("")
        self.workflow_summary.setObjectName("Muted")
        workflow_text.addWidget(self.workflow_title)
        workflow_text.addWidget(self.workflow_summary)
        workflow_layout.addLayout(workflow_text, 2)

        self.workflow_progress = AdaptiveTextProgressBar()
        self.workflow_progress.setObjectName("WorkflowProgress")
        self.workflow_progress.setTextVisible(True)
        self.workflow_progress.setMinimumWidth(260)
        workflow_layout.addWidget(self.workflow_progress, 2)

        self.workflow_status = QLabel("")
        self.workflow_status.setObjectName("Muted")
        workflow_layout.addWidget(self.workflow_status, 2)
        root.addWidget(self.workflow_card)

        self.funding_card = QFrame()
        self.funding_card.setObjectName("Card")
        funding_layout = QVBoxLayout(self.funding_card)
        funding_layout.setContentsMargins(16, 12, 16, 12)
        funding_layout.setSpacing(8)

        funding_row = QHBoxLayout()
        funding_row.setSpacing(18)

        funding_heading = QVBoxLayout()
        funding_title = QLabel("Bills Checking funding")
        funding_title.setObjectName("SectionTitle")
        self.funding_account_label = QLabel("")
        self.funding_account_label.setObjectName("Muted")
        funding_heading.addWidget(funding_title)
        funding_heading.addWidget(self.funding_account_label)
        funding_row.addLayout(funding_heading, 2)

        self.bills_balance_value = self._compact_metric(
            funding_row, "Balance"
        )
        self.first_transfer_value = self._compact_metric(
            funding_row, "1st transfer"
        )
        self.fifteenth_transfer_value = self._compact_metric(
            funding_row, "15th transfer"
        )
        self.month_transfer_value = self._compact_metric(
            funding_row, "Month"
        )

        self.funding_details_button = QPushButton("Details")
        self.funding_details_button.setObjectName("SecondaryButton")
        self.funding_details_button.setCheckable(True)
        self.funding_details_button.toggled.connect(
            self._toggle_funding_details
        )
        funding_row.addWidget(self.funding_details_button)
        funding_layout.addLayout(funding_row)

        self.funding_detail = QLabel("")
        self.funding_detail.setObjectName("Muted")
        self.funding_detail.setWordWrap(True)
        self.funding_detail.setVisible(False)
        funding_layout.addWidget(self.funding_detail)
        root.addWidget(self.funding_card)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 14, 16, 16)
        table_layout.setSpacing(8)

        table_header = QHBoxLayout()
        self.table_title = QLabel("")
        self.table_title.setObjectName("SectionTitle")
        table_note = QLabel(
            "Check Paid as you handle a bill. Bank verification stays separate."
        )
        table_note.setObjectName("Muted")
        table_header.addWidget(self.table_title)
        table_header.addStretch(1)
        table_header.addWidget(table_note)
        table_layout.addLayout(table_header)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "Paid ✓",
                "Bill",
                "Cycle",
                "When",
                "Due",
                "Paid amount",
                "Method",
                "Payment account",
                "Bank status",
                "Status",
                "Extra / Short",
            ]
        )
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
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
            (
                80,   # Paid ✓
                240,  # Bill
                90,   # Cycle
                115,  # When
                110,  # Due
                125,  # Paid amount
                145,  # Method
                210,  # Payment account
                170,  # Bank status
                130,  # Status
                120,  # Extra / Short
            ),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        self.table.itemSelectionChanged.connect(
            self._selection_changed
        )
        self.table.itemChanged.connect(
            self._manual_paid_changed
        )
        self.table.setMinimumHeight(470)
        table_layout.addWidget(self.table)
        root.addWidget(table_card, 1)

        inline_note = QLabel(
            "Select a row to edit When, Due, Paid amount, Method, Status, "
            "or Extra / Short directly in the table. Changes save when "
            "the cell edit is committed."
        )
        inline_note.setObjectName("Muted")
        inline_note.setWordWrap(True)
        root.addWidget(inline_note)
        root.addStretch(1)

        self._load_years()
        self.year.blockSignals(True)
        self.month.blockSignals(True)
        if self.year.findText(str(today.year)) >= 0:
            self.year.setCurrentText(str(today.year))
        self.month.setCurrentIndex(today.month - 1)
        self.year.blockSignals(False)
        self.month.blockSignals(False)
        self.period_buttons[self.current_view].setChecked(True)
        if auto_refresh:
            self.refresh()

    def _compact_metric(self, layout: QHBoxLayout, label_text: str) -> QLabel:
        box = QVBoxLayout()
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel("—")
        value.setObjectName("SectionTitle")
        box.addWidget(label)
        box.addWidget(value)
        layout.addLayout(box, 1)
        return value

    def _load_years(self) -> None:
        current = self.year.currentText()
        years = self.database.available_years()
        if not years:
            years = [date.today().year]

        self.year.blockSignals(True)
        self.year.clear()
        self.year.addItems([str(year) for year in years])
        if current and self.year.findText(current) >= 0:
            self.year.setCurrentText(current)
        self.year.blockSignals(False)

    def _selected_period(self) -> tuple[int, int]:
        year = int(self.year.currentText())
        month = self.month.currentIndex() + 1
        return year, month

    def set_view(
        self,
        view: str,
        *,
        refresh: bool = True,
    ) -> None:
        if view not in {"1st", "15th", "month"}:
            raise ValueError("view must be 1st, 15th, or month")
        self.current_view = view
        self.period_buttons[view].setChecked(True)
        if refresh:
            self.refresh()

    def _toggle_funding_details(self, checked: bool) -> None:
        self.funding_detail.setVisible(checked)
        self.funding_details_button.setText(
            "Hide details" if checked else "Details"
        )

    def refresh(self) -> None:
        if not self.year.currentText():
            return

        year, month = self._selected_period()
        self.database.materialize_active_bills(year, month)
        previous_selection = self.selected_instance_id
        self._fill_table(year, month)

        whole = self.database.month_summary(year, month)
        cycle = None if self.current_view == "month" else self.current_view
        progress = self.database.workflow_progress(
            year, month, cycle, active_only=True
        )
        remaining = max(progress.bill_count - progress.handled_count, 0)

        if self.current_view == "month":
            view_label = "Whole Month"
        else:
            view_label = f"{self.current_view} Pay Period"

        self.workflow_title.setText(
            f"{MONTH_NAMES[month - 1]} {year} · {view_label}"
        )
        self.workflow_summary.setText(
            f"{progress.handled_count} of {progress.bill_count} handled · "
            f"{remaining} remaining · {progress.bank_verified_count} bank verified"
        )
        self.workflow_progress.setMaximum(max(progress.bill_count, 1))
        self.workflow_progress.setValue(progress.handled_count)
        self.workflow_progress.setFormat(
            f"{progress.handled_count} / {progress.bill_count} handled"
            if progress.bill_count
            else "No bills"
        )
        self.month_total.setText(
            f"{MONTH_NAMES[month - 1]} {year}: "
            f"{schedule_balance_phrase(whole.due_cents, whole.paid_cents)}"
        )

        self._refresh_funding(year, month)

        if previous_selection is not None:
            self._restore_selection(previous_selection)

    def _refresh_funding(self, year: int, month: int) -> None:
        funding = build_funding_plan(self.database, year, month)

        if funding.environment is None:
            self.funding_account_label.setText("Waiting for bank connection")
            self.bills_balance_value.setText("—")
            self.first_transfer_value.setText("—")
            self.fifteenth_transfer_value.setText("—")
            self.month_transfer_value.setText("—")
            self.funding_detail.setText(
                "Payment Account and Bills funding stay unavailable until bank accounts are connected."
            )
            return

        if funding.bills_account_name:
            account_label = funding.bills_account_name
            if funding.bills_account_mask:
                account_label += f" ••••{funding.bills_account_mask}"
            self.funding_account_label.setText(account_label)
        else:
            self.funding_account_label.setText("Bills Checking not designated")

        self.bills_balance_value.setText(
            money(funding.available_balance_cents)
            if funding.available_balance_cents is not None
            else "—"
        )
        self.first_transfer_value.setText(money(funding.first_transfer_cents))
        self.fifteenth_transfer_value.setText(
            money(funding.fifteenth_transfer_cents)
        )
        self.month_transfer_value.setText(money(funding.total_transfer_cents))

        detail_parts = [
            f"{funding.included_bill_count} bill(s) still need Bills-account funding",
            f"{money(funding.total_required_cents)} gross remaining",
        ]
        if funding.balance_applied:
            detail_parts.append("current Bills balance applied 1st, then 15th")
        elif funding.available_balance_cents is not None:
            detail_parts.append(
                "current bank balance not applied outside the current month"
            )
        else:
            detail_parts.append("no synced Bills balance available")

        if funding.source_totals:
            source_text = ", ".join(
                f"{source}: {money(amount)}"
                for source, amount in funding.source_totals
            )
            detail_parts.append(f"transfer sources — {source_text}")

        if funding.review_bill_count:
            detail_parts.append(
                f"{funding.review_bill_count} bill(s) need account/transfer review"
            )

        self.funding_detail.setText(" · ".join(detail_parts))

    def _rows_for_view(self, year: int, month: int):
        if self.current_view == "month":
            return self.database.list_month_instances(
                year, month, active_only=True
            )
        return self.database.list_cycle_instances(
            year, month, self.current_view, active_only=True
        )

    def _fill_table(self, year: int, month: int) -> None:
        rows = self._rows_for_view(year, month)
        if self.unfinished_only.isChecked():
            rows = [
                row
                for row in rows
                if not int(row["manually_paid"] or 0)
                and not int(row["bank_verified"] or 0)
            ]

        self.table_title.setText(
            {
                "1st": "1st Pay Period checklist",
                "15th": "15th Pay Period checklist",
                "month": "Whole Month checklist",
            }[self.current_view]
        )
        self.table.setColumnHidden(2, self.current_view != "month")

        self._filling_table = True
        self.table.blockSignals(True)
        sorting = begin_table_refresh(self.table)
        try:
            self.table.setRowCount(len(rows))
            resume_marked = False

            for row_index, row in enumerate(rows):
                manually_paid = bool(int(row["manually_paid"] or 0))
                bank_verified = bool(int(row["bank_verified"] or 0))
                handled = manually_paid or bank_verified
                resume_here = not handled and not resume_marked
                if resume_here:
                    resume_marked = True

                payment_account = (
                    row["payment_account_name"]
                    or row["payment_account_snapshot"]
                    or row["payment_account"]
                    or "—"
                )
                if (
                    row["payment_account_name"]
                    and row["payment_account_mask"]
                ):
                    payment_account = (
                        f"{row['payment_account_name']} "
                        f"••••{row['payment_account_mask']}"
                    )

                bank_status = bank_status_text(
                    manually_paid=manually_paid,
                    bank_verified=bank_verified,
                    year=year,
                    month=month,
                )
                bill_display = str(row["bill_name_snapshot"])
                if resume_here:
                    bill_display = f"→ {bill_display}"

                values = [
                    ("", int(manually_paid)),
                    (
                        bill_display,
                        str(row["bill_name_snapshot"]).casefold(),
                    ),
                    (row["cycle"], str(row["cycle"])),
                    (
                        row["when_label"] or "",
                        str(row["when_label"] or "").casefold(),
                    ),
                    (
                        money(row["due_cents"]),
                        -1
                        if row["due_cents"] is None
                        else int(row["due_cents"]),
                    ),
                    (
                        money(row["paid_cents"]),
                        -1
                        if row["paid_cents"] is None
                        else int(row["paid_cents"]),
                    ),
                    (
                        row["method"] or "",
                        str(row["method"] or "").casefold(),
                    ),
                    (
                        payment_account,
                        str(payment_account).casefold(),
                    ),
                    (bank_status, 1 if bank_verified else 0),
                    (
                        row["status"] or "",
                        str(row["status"] or "").casefold(),
                    ),
                    (
                        decimal_text(row["extra_short"]),
                        decimal_text(row["extra_short"]),
                    ),
                ]

                for col, (value, sort_value) in enumerate(values):
                    item = SortableTableWidgetItem(
                        str(value),
                        sort_value=sort_value,
                        user_data=int(row["id"]),
                    )
                    base_flags = (
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    if col == 0:
                        item.setFlags(
                            base_flags
                            | Qt.ItemFlag.ItemIsUserCheckable
                        )
                        item.setCheckState(
                            Qt.CheckState.Checked
                            if manually_paid
                            else Qt.CheckState.Unchecked
                        )
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter
                        )
                        item.setToolTip(
                            "Manual checkpoint only. It does not change "
                            "the Paid amount or bank reconciliation."
                        )
                    else:
                        item.setFlags(base_flags)

                    if col in {4, 5, 10}:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight
                            | Qt.AlignmentFlag.AlignVCenter
                        )

                    if bank_verified:
                        item.setBackground(QColor("#14241b"))
                    elif manually_paid:
                        item.setBackground(QColor("#17231d"))
                    elif resume_here:
                        item.setBackground(QColor("#262815"))
                        if col != 8:
                            item.setToolTip(
                                "Resume here — this is the first "
                                "unfinished bill in the current list."
                            )

                    if col == 8:
                        item.setToolTip(
                            verification_provenance_text(row)
                        )
                        if bank_verified:
                            item.setForeground(QColor("#c8ff3d"))

                    self.table.setItem(row_index, col, item)
        finally:
            end_table_refresh(self.table, sorting)
            self.table.blockSignals(False)
            self._filling_table = False


    def _selected_row_index(self) -> int | None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        return int(selected[0].row())

    def _selection_changed(self) -> None:
        row = self._selected_row_index()
        self._remove_inline_editors()
        if row is None:
            self.selected_instance_id = None
            return

        item = self.table.item(row, 1)
        if item is None:
            return
        raw_id = item.data(Qt.ItemDataRole.UserRole)
        if raw_id is None:
            return

        self.selected_instance_id = int(raw_id)
        instance = self.database.get_bill_instance(
            self.selected_instance_id
        )
        if instance is None:
            return

        self._install_inline_editors(row, instance)
        self.workflow_status.setText(
            verification_provenance_text(instance)
        )

    def _install_inline_editors(self, row: int, instance) -> None:
        values = {
            3: str(instance["when_label"] or ""),
            4: money_input(instance["due_cents"]),
            5: money_input(instance["paid_cents"]),
            6: str(instance["method"] or ""),
            9: str(instance["status"] or ""),
            10: decimal_text(instance["extra_short"]),
        }
        self._inline_editor_row = row
        self._inline_editor_instance_id = int(instance["id"])
        self._inline_editors: dict[int, QLineEdit] = {}

        self.table.setRowHeight(row, 42)
        minimum_widths = {
            3: 100,
            4: 96,
            5: 108,
            6: 118,
            9: 108,
            10: 100,
        }

        for column, value in values.items():
            editor = QLineEdit()
            editor.setObjectName("InlineCellEditor")
            editor.setText(value)
            editor.setMinimumWidth(minimum_widths[column])
            editor.setMinimumHeight(30)
            editor.setProperty("inlineColumn", column)
            editor.editingFinished.connect(
                lambda c=column, e=editor:
                    self._commit_inline_field(c, e)
            )
            self.table.setCellWidget(row, column, editor)
            self._inline_editors[column] = editor

    def _remove_inline_editors(self) -> None:
        row = getattr(self, "_inline_editor_row", None)
        if row is not None and 0 <= row < self.table.rowCount():
            for column in (3, 4, 5, 6, 9, 10):
                self.table.removeCellWidget(row, column)
            self.table.setRowHeight(row, 30)
        self._inline_editor_row = None
        self._inline_editor_instance_id = None
        self._inline_editors = {}

    def _commit_inline_field(
        self,
        column: int,
        editor: QLineEdit,
    ) -> None:
        instance_id = getattr(
            self,
            "_inline_editor_instance_id",
            None,
        )
        if instance_id is None:
            return

        instance = self.database.get_bill_instance(
            int(instance_id)
        )
        if instance is None:
            return

        when_label = instance["when_label"]
        due_cents = instance["due_cents"]
        paid_cents = instance["paid_cents"]
        method = instance["method"]
        status = instance["status"]
        extra_short = instance["extra_short"]

        try:
            if column == 3:
                when_label = editor.text().strip() or None
            elif column == 4:
                due_cents = parse_money(editor.text())
            elif column == 5:
                paid_cents = parse_money(editor.text())
            elif column == 6:
                method = editor.text().strip() or None
            elif column == 9:
                status = editor.text().strip() or None
            elif column == 10:
                extra_short = editor.text().strip() or None
            else:
                return
        except ValueError as exc:
            self.workflow_status.setText(
                f"Inline edit not saved: {exc}"
            )
            self._reset_inline_editor(column, instance)
            return

        try:
            self.backup_manager.create_backup(
                "pre-pay-period-inline-edit"
            )
            self.database.update_bill_instance(
                int(instance_id),
                when_label=when_label,
                due_cents=(
                    None
                    if due_cents is None
                    else int(due_cents)
                ),
                paid_cents=(
                    None
                    if paid_cents is None
                    else int(paid_cents)
                ),
                method=method,
                status=status,
                extra_short=(
                    None
                    if extra_short in (None, "")
                    else str(extra_short)
                ),
                payment_account_snapshot=(
                    instance["payment_account_snapshot"]
                ),
            )
        except Exception as exc:
            self.workflow_status.setText(
                f"Inline edit not saved: {exc}"
            )
            self._reset_inline_editor(column, instance)
            return

        self._update_inline_display(
            column,
            when_label=when_label,
            due_cents=due_cents,
            paid_cents=paid_cents,
            method=method,
            status=status,
            extra_short=extra_short,
        )
        self.workflow_status.setText(
            "Bill row saved to SQLite."
        )
        self.on_changed()

        if column in {4, 5}:
            # Amount edits affect the visible workflow/funding totals.
            self.selected_instance_id = int(instance_id)
            self.refresh()

    def _reset_inline_editor(self, column: int, instance) -> None:
        editor = getattr(self, "_inline_editors", {}).get(column)
        if editor is None:
            return
        values = {
            3: str(instance["when_label"] or ""),
            4: money_input(instance["due_cents"]),
            5: money_input(instance["paid_cents"]),
            6: str(instance["method"] or ""),
            9: str(instance["status"] or ""),
            10: decimal_text(instance["extra_short"]),
        }
        editor.setText(values[column])

    def _update_inline_display(
        self,
        column: int,
        *,
        when_label,
        due_cents,
        paid_cents,
        method,
        status,
        extra_short,
    ) -> None:
        row = getattr(self, "_inline_editor_row", None)
        if row is None:
            return
        item = self.table.item(row, column)
        if item is None:
            return

        values = {
            3: str(when_label or ""),
            4: money(due_cents),
            5: money(paid_cents),
            6: str(method or ""),
            9: str(status or ""),
            10: decimal_text(extra_short),
        }
        self._filling_table = True
        try:
            item.setText(values[column])
        finally:
            self._filling_table = False

    def _manual_paid_changed(
        self,
        item: SortableTableWidgetItem,
    ) -> None:
        if self._filling_table or item.column() != 0:
            return

        raw_id = item.data(Qt.ItemDataRole.UserRole)
        if raw_id is None:
            return

        instance_id = int(raw_id)
        manually_paid = (
            item.checkState() == Qt.CheckState.Checked
        )
        try:
            self.database.set_bill_manually_paid(
                instance_id,
                manually_paid,
            )
        except Exception as exc:
            self.workflow_status.setText(
                f"Could not update manual Paid checkpoint: {exc}"
            )
            self.refresh()
            return

        self.selected_instance_id = instance_id
        self.workflow_status.setText(
            "Paid checkpoint saved."
            if manually_paid
            else "Paid checkpoint cleared."
        )
        self.on_changed()
        self.refresh()

    def _restore_selection(self, instance_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if (
                item is not None
                and int(item.data(Qt.ItemDataRole.UserRole))
                == instance_id
            ):
                self.table.selectRow(row)
                return

        if self.unfinished_only.isChecked():
            self.selected_instance_id = None
            self._remove_inline_editors()

    def _set_selected_paid(self, paid: bool) -> None:
        row = self._selected_row_index()
        if row is None:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Checked
            if paid
            else Qt.CheckState.Unchecked
        )

    def _focus_inline_editor(self, column: int) -> None:
        editor = getattr(self, "_inline_editors", {}).get(column)
        if editor is not None:
            editor.setFocus()
            editor.selectAll()

    def _show_table_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, column = context

        if column in {3, 4, 5, 6, 9, 10}:
            edit_cell = menu.addAction("Edit this cell")
            edit_cell.triggered.connect(
                lambda checked=False, selected=column:
                    self._focus_inline_editor(selected)
            )

        paid_item = self.table.item(row, 0)
        if paid_item is not None:
            is_paid = (
                paid_item.checkState()
                == Qt.CheckState.Checked
            )
            paid_action = menu.addAction(
                "Clear Paid checkpoint"
                if is_paid
                else "Mark Paid"
            )
            paid_action.triggered.connect(
                lambda checked=False, value=not is_paid:
                    self._set_selected_paid(value)
            )

        verification_item = self.table.item(row, 8)
        if (
            verification_item is not None
            and verification_item.toolTip()
        ):
            copy_verification = menu.addAction(
                "Copy bank verification details"
            )
            copy_verification.triggered.connect(
                lambda checked=False,
                value=verification_item.toolTip():
                    copy_text(value)
            )

        menu.addSeparator()
        unfinished = menu.addAction(
            "Show all bills"
            if self.unfinished_only.isChecked()
            else "Show unfinished only"
        )
        unfinished.triggered.connect(
            lambda checked=False:
                self.unfinished_only.setChecked(
                    not self.unfinished_only.isChecked()
                )
        )

        view_menu = menu.addMenu("Switch work list")
        for view, label in (
            ("1st", "1st Pay Period"),
            ("15th", "15th Pay Period"),
            ("month", "Whole Month"),
        ):
            action = view_menu.addAction(label)
            action.setEnabled(view != self.current_view)
            action.triggered.connect(
                lambda checked=False, selected=view:
                    self.set_view(selected)
            )

        menu.addSeparator()
        refresh = menu.addAction("Refresh Pay Periods")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.table, menu, position)

