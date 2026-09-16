from __future__ import annotations

from datetime import date
from typing import Callable

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..backups import BackupManager
from ..bank_connection import connection_state
from ..branding import brand_logo_pixmap
from ..database import Database
from ..settings import load_user_ui_preferences, save_user_ui_preferences
from .context_menu import (
    begin_table_context_menu,
    copy_text,
    show_table_context_menu,
)
from .transactions_page import TransactionsPage
from .formatting import money, schedule_balance
from .overview_chart import DonutBreakdownChart
from .pay_periods import PayPeriodsPage
from .reconciliation_page import ReconciliationPage
from .reports_page import ReportsPage
from .settings_page import SettingsPage
from .table_sort import (
    SortableTableWidgetItem,
    begin_table_refresh,
    configure_resizable_columns,
    end_table_refresh,
    set_sortable,
)


class SidebarNavDelegate(QStyledItemDelegate):
    """Draw the sidebar drag grip only while sidebar editing is enabled."""

    def __init__(self, nav_list: "SidebarNavList"):
        super().__init__(nav_list)
        self.nav_list = nav_list

    EDITOR_LEFT_INSET = 10
    EDITOR_RIGHT_GAP = 8
    EDITOR_MIN_HEIGHT = 34
    EDITOR_MAX_HEIGHT = 38
    EDITOR_VERTICAL_MARGIN = 4

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setObjectName("SidebarNavEditor")
        editor.setMinimumHeight(self.EDITOR_MIN_HEIGHT)
        editor.setMaximumHeight(self.EDITOR_MAX_HEIGHT)
        return editor

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if self.nav_list.edit_mode:
            size.setHeight(
                max(
                    size.height(),
                    self.EDITOR_MIN_HEIGHT
                    + self.EDITOR_VERTICAL_MARGIN * 2,
                )
            )
        return size

    def updateEditorGeometry(
        self,
        editor,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        rect = option.rect
        if not self.nav_list.edit_mode:
            super().updateEditorGeometry(editor, option, index)
            return

        left = rect.left() + self.EDITOR_LEFT_INSET
        right = (
            rect.right()
            - self.nav_list.HANDLE_WIDTH
            - self.EDITOR_RIGHT_GAP
        )
        width = max(48, right - left + 1)
        available_height = max(
            22,
            rect.height() - self.EDITOR_VERTICAL_MARGIN * 2,
        )
        font_height = editor.fontMetrics().height() + 12
        desired_height = max(
            self.EDITOR_MIN_HEIGHT,
            font_height,
        )
        height = min(
            self.EDITOR_MAX_HEIGHT,
            available_height,
            desired_height,
        )
        top = rect.center().y() - height // 2
        editor.setGeometry(left, top, width, height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        if not self.nav_list.edit_mode:
            super().paint(painter, option, index)
            return

        text_option = QStyleOptionViewItem(option)
        text_option.rect = option.rect.adjusted(
            0,
            0,
            -self.nav_list.HANDLE_WIDTH,
            0,
        )
        super().paint(painter, text_option, index)

        selected = bool(
            option.state & QStyle.StateFlag.State_Selected
        )
        color = (
            option.palette.highlightedText().color()
            if selected
            else option.palette.text().color()
        )
        painter.save()
        pen = painter.pen()
        pen.setColor(color)
        pen.setWidth(2)
        painter.setPen(pen)

        right = option.rect.right() - 10
        center_y = option.rect.center().y()
        for offset in (-5, 0, 5):
            painter.drawLine(
                right - 12,
                center_y + offset,
                right,
                center_y + offset,
            )

        insertion_row = self.nav_list.drop_insertion_row
        row = index.row()
        if insertion_row == row:
            drop_pen = painter.pen()
            drop_pen.setColor(option.palette.highlight().color())
            drop_pen.setWidth(2)
            painter.setPen(drop_pen)
            painter.drawLine(
                option.rect.left() + 6,
                option.rect.top(),
                option.rect.right() - 6,
                option.rect.top(),
            )
        elif (
            insertion_row == self.nav_list.count()
            and row == self.nav_list.count() - 1
        ):
            drop_pen = painter.pen()
            drop_pen.setColor(option.palette.highlight().color())
            drop_pen.setWidth(2)
            painter.setPen(drop_pen)
            painter.drawLine(
                option.rect.left() + 6,
                option.rect.bottom(),
                option.rect.right() - 6,
                option.rect.bottom(),
            )
        painter.restore()


class SidebarNavList(QListWidget):
    """Navigation list with safe insert-only reordering from the grip."""

    HANDLE_WIDTH = 34
    orderChanged = Signal()

    def __init__(self):
        super().__init__()
        self.edit_mode = False
        self._drag_from_handle = False
        self._drag_source_row = -1
        self._selection_before_drag: QListWidgetItem | None = None
        self.drop_insertion_row: int | None = None
        self._delegate = SidebarNavDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setSpacing(2)
        self.set_edit_mode(False)

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)
        self._clear_drag_state(restore_selection=True)
        if self.edit_mode:
            self.setDragDropMode(
                QAbstractItemView.DragDropMode.InternalMove
            )
            self.setDragEnabled(True)
            self.setAcceptDrops(True)
            # A custom between-row line is clearer than Qt's on-item marker.
            self.setDropIndicatorShown(False)
            self.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
        else:
            self.setDragDropMode(
                QAbstractItemView.DragDropMode.NoDragDrop
            )
            self.setDragEnabled(False)
            self.setAcceptDrops(False)
            self.setDropIndicatorShown(False)
            self.setEditTriggers(
                QAbstractItemView.EditTrigger.NoEditTriggers
            )
        self.doItemsLayout()
        self.viewport().update()

    def is_drag_handle_position(self, point: QPoint) -> bool:
        if not self.edit_mode:
            return False
        index = self.indexAt(point)
        if not index.isValid():
            return False
        rect = self.visualRect(index)
        return point.x() >= rect.right() - self.HANDLE_WIDTH

    def insertion_row_for_position(self, point: QPoint) -> int:
        """Return a between-row insertion index; never an on-item target."""
        if self.count() == 0:
            return 0
        index = self.indexAt(point)
        if not index.isValid():
            first = self.visualItemRect(self.item(0))
            if point.y() < first.top():
                return 0
            return self.count()
        rect = self.visualRect(index)
        if point.y() < rect.center().y():
            return index.row()
        return index.row() + 1

    def move_item_to_row(
        self,
        source_row: int,
        insertion_row: int,
        *,
        preserve_item: QListWidgetItem | None = None,
    ) -> bool:
        """Move one row without ever replacing/dropping onto another row."""
        if not (0 <= source_row < self.count()):
            return False

        insertion_row = max(0, min(int(insertion_row), self.count()))
        destination_row = insertion_row
        if source_row < destination_row:
            destination_row -= 1

        if destination_row == source_row:
            return False

        signals_were_blocked = self.blockSignals(True)
        try:
            moved = self.takeItem(source_row)
            if moved is None:
                return False

            destination_row = max(
                0,
                min(destination_row, self.count()),
            )
            self.insertItem(destination_row, moved)

            if preserve_item is not None:
                self.setCurrentItem(preserve_item)
        finally:
            self.blockSignals(signals_were_blocked)

        # Emit one intentional layout-change signal after the row is stable.
        self.orderChanged.emit()
        return True

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        self._drag_from_handle = self.is_drag_handle_position(point)
        self._drag_source_row = (
            self.indexAt(point).row()
            if self._drag_from_handle
            else -1
        )
        self._selection_before_drag = (
            self.currentItem()
            if self._drag_from_handle
            else None
        )

        if self._drag_from_handle:
            # Selecting a grip is an editing action, not navigation.
            self.blockSignals(True)
            super().mousePressEvent(event)
            self.blockSignals(False)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._drag_from_handle:
            self._restore_selection_before_drag()
        self._clear_drag_state()

    def startDrag(self, supported_actions) -> None:
        if not (
            self.edit_mode
            and self._drag_from_handle
            and self._drag_source_row >= 0
        ):
            return

        # Do not call QListWidget.startDrag() here. In InternalMove mode Qt
        # owns the source item and may remove/recreate it after a move. Our
        # dropEvent already performs the move explicitly, so delegating to
        # QListWidget caused the source row to be deleted a second time and
        # left stale Python QListWidgetItem wrappers behind.
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(
            "application/x-biweekly-bills-sidebar-row",
            str(self._drag_source_row).encode("ascii"),
        )
        drag.setMimeData(mime)

        source_item = self.item(self._drag_source_row)
        if source_item is not None:
            rect = self.visualItemRect(source_item)
            if rect.isValid():
                pixmap = self.viewport().grab(rect)
                drag.setPixmap(pixmap)
                drag.setHotSpot(
                    QPoint(
                        max(0, rect.width() - self.HANDLE_WIDTH // 2),
                        rect.height() // 2,
                    )
                )

        try:
            drag.exec(
                Qt.DropAction.MoveAction,
                Qt.DropAction.MoveAction,
            )
        finally:
            # The item remains owned by this list throughout the custom drag,
            # so restoring selection is safe even after a cancelled drop.
            self._restore_selection_before_drag()
            self._clear_drag_state()

    def dragEnterEvent(self, event) -> None:
        if self.edit_mode and event.source() is self:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not (self.edit_mode and event.source() is self):
            event.ignore()
            return
        self.drop_insertion_row = self.insertion_row_for_position(
            event.position().toPoint()
        )
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.viewport().update()

    def dragLeaveEvent(self, event) -> None:
        self.drop_insertion_row = None
        self.viewport().update()
        event.accept()

    def dropEvent(self, event) -> None:
        if not (
            self.edit_mode
            and event.source() is self
            and self._drag_source_row >= 0
        ):
            event.ignore()
            return

        insertion_row = self.insertion_row_for_position(
            event.position().toPoint()
        )
        self.move_item_to_row(
            self._drag_source_row,
            insertion_row,
            preserve_item=self._selection_before_drag,
        )
        self.drop_insertion_row = None
        self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def _restore_selection_before_drag(self) -> None:
        if self._selection_before_drag is None:
            return
        self.blockSignals(True)
        self.setCurrentItem(self._selection_before_drag)
        self.blockSignals(False)

    def _clear_drag_state(self, *, restore_selection: bool = False) -> None:
        if restore_selection:
            self._restore_selection_before_drag()
        self._drag_from_handle = False
        self._drag_source_row = -1
        self._selection_before_drag = None
        self.drop_insertion_row = None
        self.viewport().update()


class DashboardPage(QWidget):
    open_pay_period = Signal(str)

    def __init__(
        self,
        database: Database,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.current = date.today()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 22)
        root.setSpacing(12)

        period_bar = QHBoxLayout()
        period_bar.setSpacing(10)
        period_bar.addWidget(QLabel("Month"))
        self.month_selector = QComboBox()
        self.month_selector.addItems(
            [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ]
        )
        period_bar.addWidget(self.month_selector)
        period_bar.addWidget(QLabel("Year"))
        self.year_selector = QComboBox()
        years = self.database.available_years() or [self.current.year]
        self.year_selector.addItems([str(year) for year in years])
        period_bar.addWidget(self.year_selector)
        period_bar.addStretch(1)
        root.addLayout(period_bar)

        if self.year_selector.findText(str(self.current.year)) >= 0:
            self.year_selector.setCurrentText(str(self.current.year))
        self.month_selector.setCurrentIndex(self.current.month - 1)
        self.month_selector.currentIndexChanged.connect(self.refresh)
        self.year_selector.currentIndexChanged.connect(self.refresh)

        self.hero = QFrame()
        self.hero.setObjectName("HeroCard")
        self.hero.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(18, 14, 18, 14)
        hero_layout.setSpacing(16)

        left = QVBoxLayout()
        eyebrow = QLabel("THIS MONTH")
        eyebrow.setObjectName("MetricLabel")
        self.month_title = QLabel()
        self.month_title.setObjectName("SectionTitle")
        self.balance_label = QLabel("Remaining")
        self.balance_label.setObjectName("MetricLabel")
        self.remaining = QLabel()
        self.remaining.setObjectName("AccentValue")
        self.progress_text = QLabel()
        self.progress_text.setObjectName("Muted")
        self.closeout_text = QLabel()
        self.closeout_text.setObjectName("Muted")
        self.closeout_text.setWordWrap(True)
        left.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(eyebrow)
        title_row.addWidget(self.month_title)
        title_row.addStretch(1)
        left.addLayout(title_row)
        left.addWidget(self.balance_label)
        left.addWidget(self.remaining)
        left.addSpacing(2)
        left.addWidget(self.progress_text)
        left.addWidget(self.closeout_text)
        hero_layout.addLayout(left, 5)

        self.scheduled_chart = DonutBreakdownChart()
        self.paid_chart = DonutBreakdownChart()
        self.due_card = self._metric_card(
            "Scheduled",
            "—",
            self.scheduled_chart,
        )
        self.paid_card = self._metric_card(
            "Paid",
            "—",
            self.paid_chart,
        )
        hero_layout.addWidget(self.due_card, 3)
        hero_layout.addWidget(self.paid_card, 3)
        root.addWidget(self.hero)

        periods = QHBoxLayout()
        periods.setSpacing(12)
        self.first_card = self._period_card("1st Pay Period", "1st")
        self.fifteenth_card = self._period_card("15th Pay Period", "15th")
        periods.addWidget(self.first_card)
        periods.addWidget(self.fifteenth_card)
        root.addLayout(periods)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(18, 16, 18, 18)
        table_layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Bill activity")
        title.setObjectName("SectionTitle")
        subtitle = QLabel("Current month · imported history and app-managed bills")
        subtitle.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(subtitle)
        table_layout.addLayout(header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Bill", "Cycle", "Due", "Paid", "Method", "Status", "Funding"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.table,
            (220, 90, 105, 105, 150, 125, 190),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        table_layout.addWidget(self.table)
        root.addWidget(table_card, 1)

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
        menu, row, _column = context

        bill_item = self.table.item(row, 0)
        cycle_item = self.table.item(row, 1)
        if bill_item is not None:
            copy_bill = menu.addAction("Copy bill name")
            copy_bill.triggered.connect(
                lambda checked=False, value=bill_item.text():
                    copy_text(value.removeprefix("→ ").strip())
            )

        cycle = cycle_item.text() if cycle_item is not None else ""
        if cycle in {"1st", "15th"}:
            open_period = menu.addAction(
                f"Open {cycle} Pay Period"
            )
            open_period.triggered.connect(
                lambda checked=False, selected_cycle=cycle:
                    self.open_pay_period.emit(selected_cycle)
            )

        menu.addSeparator()
        refresh = menu.addAction("Refresh overview")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.table, menu, position)

    def _metric_card(
        self,
        label_text: str,
        value_text: str,
        chart: DonutBreakdownChart,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 10)
        layout.setSpacing(3)
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel(value_text)
        value.setObjectName("MetricValue")
        layout.addWidget(label)
        layout.addWidget(value)
        layout.addWidget(chart)
        card.value_label = value  # type: ignore[attr-defined]
        return card

    def _period_card(self, title_text: str, cycle: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(3)

        title = QLabel(title_text)
        title.setObjectName("SectionTitle")

        amount = QLabel("—")
        amount.setObjectName("MetricValue")

        detail = QLabel("No imported bills yet")
        detail.setObjectName("Muted")

        bar = QFrame()
        bar.setObjectName("AccentBar")
        bar.setFixedHeight(7)

        layout.addWidget(title)
        layout.addWidget(amount)
        layout.addWidget(detail)
        layout.addWidget(bar)

        open_button = QPushButton("Open checklist")
        open_button.setObjectName("SecondaryButton")
        open_button.clicked.connect(
            lambda checked=False, selected_cycle=cycle:
                self.open_pay_period.emit(selected_cycle)
        )
        layout.addWidget(open_button)

        card.amount_label = amount  # type: ignore[attr-defined]
        card.detail_label = detail  # type: ignore[attr-defined]
        return card

    def refresh(self) -> None:
        year = int(self.year_selector.currentText()) if self.year_selector.currentText() else self.current.year
        month = self.month_selector.currentIndex() + 1
        summary = self.database.month_summary(year, month)
        first = self.database.cycle_summary(year, month, "1st")
        fifteenth = self.database.cycle_summary(year, month, "15th")

        self.month_title.setText(
            f"{self.month_selector.currentText()} {year}"
        )
        balance_label, balance_amount = schedule_balance(
            summary.due_cents, summary.paid_cents
        )
        self.balance_label.setText(balance_label)
        self.remaining.setText(money(balance_amount))
        workflow = self.database.workflow_progress(year, month)
        self.progress_text.setText(
            f"{workflow.handled_count} of {workflow.bill_count} handled · "
            f"{workflow.bank_verified_count} bank verified"
        )
        closeout = self.database.month_closeout(year, month)
        unfinished = max(closeout.bill_count - closeout.handled_count, 0)
        self.closeout_text.setText(
            f"Closeout · {closeout.verified_count} verified · "
            f"{closeout.paid_unverified_count} paid unverified · "
            f"{unfinished} unfinished · "
            f"{money(closeout.remaining_cents)} remaining"
        )
        self.due_card.value_label.setText(money(summary.due_cents))  # type: ignore[attr-defined]
        self.paid_card.value_label.setText(money(summary.paid_cents))  # type: ignore[attr-defined]

        first_due = max(int(first.due_cents or 0), 0)
        fifteenth_due = max(int(fifteenth.due_cents or 0), 0)
        scheduled_total = first_due + fifteenth_due
        first_share = (
            round(first_due / scheduled_total * 100)
            if scheduled_total
            else 0
        )
        self.scheduled_chart.set_data(
            "1st Pay Period",
            first_due,
            "15th Pay Period",
            fifteenth_due,
            center_text=f"{first_share}%",
            center_subtext="1st share",
        )

        paid_cents = max(int(summary.paid_cents or 0), 0)
        due_cents = max(int(summary.due_cents or 0), 0)
        still_due = max(due_cents - paid_cents, 0)
        paid_percent = (
            round(paid_cents / due_cents * 100)
            if due_cents
            else 0
        )
        self.paid_chart.set_data(
            "Paid",
            paid_cents,
            "Still due",
            still_due,
            center_text=f"{paid_percent}%",
            center_subtext="of scheduled",
        )

        for card, cycle_summary, cycle in (
            (self.first_card, first, "1st"),
            (self.fifteenth_card, fifteenth, "15th"),
        ):
            cycle_label, cycle_amount = schedule_balance(
                cycle_summary.due_cents, cycle_summary.paid_cents
            )
            cycle_workflow = self.database.workflow_progress(
                year, month, cycle
            )
            card.amount_label.setText(money(cycle_amount))  # type: ignore[attr-defined]
            card.detail_label.setText(  # type: ignore[attr-defined]
                f"{cycle_label} · {cycle_workflow.handled_count}/"
                f"{cycle_workflow.bill_count} handled · "
                f"{money(cycle_summary.paid_cents)} paid"
            )

        rows = self.database.list_month_instances(year, month)
        sorting = begin_table_refresh(self.table)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            transfer_required = row["transfer_required"]
            source_label = row["transfer_source_account_name"]
            if source_label and row["transfer_source_account_mask"]:
                source_label += f" ••••{row['transfer_source_account_mask']}"

            if transfer_required is None:
                funding = "Review transfer setting"
            elif transfer_required:
                funding = source_label or "Transfer source not set"
            else:
                funding = "No transfer"

            values = [
                (row["bill_name_snapshot"], str(row["bill_name_snapshot"]).casefold()),
                (row["cycle"], str(row["cycle"])),
                (money(row["due_cents"]), -1 if row["due_cents"] is None else int(row["due_cents"])),
                (money(row["paid_cents"]), -1 if row["paid_cents"] is None else int(row["paid_cents"])),
                (row["method"] or "—", str(row["method"] or "").casefold()),
                (row["status"] or "—", str(row["status"] or "").casefold()),
                (funding, str(funding).casefold()),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(str(value), sort_value=sort_value)
                if col in {2, 3}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row_index, col, item)
        end_table_refresh(self.table, sorting)


class BillsPage(QWidget):
    """Bill maintenance using an in-page editor, never modal bill dialogs."""

    def __init__(
        self,
        database: Database,
        on_changed: Callable[[], None],
        backup_manager: BackupManager,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.on_changed = on_changed
        self.backup_manager = backup_manager
        self.selected_id: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)

        intro = QLabel(
            "Setup is now modeled as structured bill data. Edit here; historical bill instances stay intact."
        )
        intro.setObjectName("Muted")
        root.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        list_card = QFrame()
        list_card.setObjectName("Card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 16, 16, 16)
        title = QLabel("Master bill list")
        title.setObjectName("SectionTitle")
        list_layout.addWidget(title)

        self.bill_table = QTableWidget(0, 4)
        self.bill_table.setHorizontalHeaderLabels(["Bill", "Cycle", "Method", "Active"])
        self.bill_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.bill_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.bill_table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.bill_table,
            (280, 110, 170, 90),
        )
        set_sortable(self.bill_table)
        self.bill_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.bill_table.customContextMenuRequested.connect(
            self._show_bill_context_menu
        )
        self.bill_table.itemSelectionChanged.connect(self._load_selected)
        list_layout.addWidget(self.bill_table)

        controls = QHBoxLayout()
        new_button = QPushButton("New bill")
        new_button.setObjectName("SecondaryButton")
        new_button.clicked.connect(self._new_bill)
        controls.addWidget(new_button)
        controls.addStretch(1)
        list_layout.addLayout(controls)

        editor_card = QFrame()
        editor_card.setObjectName("Card")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(18, 16, 18, 18)
        editor_layout.setSpacing(10)

        title = QLabel("Bill editor")
        title.setObjectName("SectionTitle")
        editor_layout.addWidget(title)

        self.name = self._field(editor_layout, "Bill name")
        self.cycle = QComboBox()
        self.cycle.addItems(["1st", "15th", "Both"])
        self._labeled(editor_layout, "Cycle", self.cycle)
        self.latest_due = self._field(editor_layout, "Latest due / typical amount")
        self.method = self._field(editor_layout, "Default method")

        self.aliases = self._field(
            editor_layout,
            "Merchant aliases (comma separated)",
        )
        alias_help = QLabel(
            "Aliases are used by automatic and historical bank matching."
        )
        alias_help.setObjectName("Muted")
        alias_help.setWordWrap(True)
        editor_layout.addWidget(alias_help)

        self.payment_account = self._account_combo()
        self.payment_account.currentIndexChanged.connect(
            self._update_payment_account_help
        )
        self._labeled(editor_layout, "Payment account", self.payment_account)

        self.account_help = QLabel("")
        self.account_help.setObjectName("Muted")
        self.account_help.setWordWrap(True)
        editor_layout.addWidget(self.account_help)

        self.notes = QTextEdit()
        self.notes.setMinimumHeight(90)
        self._labeled(editor_layout, "Notes", self.notes)

        self.status = QLabel("Select a bill or create a new one.")
        self.status.setObjectName("Muted")
        editor_layout.addWidget(self.status)

        buttons = QHBoxLayout()
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save)
        deactivate = QPushButton("Deactivate")
        deactivate.setObjectName("DangerButton")
        deactivate.clicked.connect(self._deactivate)
        buttons.addWidget(save)
        buttons.addWidget(deactivate)
        buttons.addStretch(1)
        editor_layout.addLayout(buttons)
        editor_layout.addStretch(1)

        splitter.addWidget(list_card)
        splitter.addWidget(editor_card)
        splitter.setSizes([680, 400])
        root.addWidget(splitter, 1)

        if auto_refresh:
            self.refresh()

    def _show_bill_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.bill_table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, _column = context

        bill_item = self.bill_table.item(row, 0)
        raw_id = (
            None
            if bill_item is None
            else bill_item.data(Qt.ItemDataRole.UserRole)
        )
        bill_id = None if raw_id is None else int(raw_id)

        edit_bill = menu.addAction("Edit selected bill")
        edit_bill.triggered.connect(self.name.setFocus)

        edit_aliases = menu.addAction("Edit merchant aliases")
        edit_aliases.triggered.connect(self.aliases.setFocus)

        if bill_id is not None:
            aliases = self.database.list_bill_aliases(bill_id)
            if aliases:
                copy_aliases = menu.addAction("Copy merchant aliases")
                copy_aliases.triggered.connect(
                    lambda checked=False, values=tuple(aliases):
                        copy_text(", ".join(values))
                )

        menu.addSeparator()
        new_bill = menu.addAction("New bill")
        new_bill.triggered.connect(self._new_bill)
        refresh = menu.addAction("Refresh bill list")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.bill_table, menu, position)

    def _labeled(self, layout: QVBoxLayout, label_text: str, widget: QWidget) -> None:
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        layout.addWidget(label)
        layout.addWidget(widget)

    def _field(self, layout: QVBoxLayout, label_text: str) -> QLineEdit:
        field = QLineEdit()
        self._labeled(layout, label_text, field)
        return field

    def _account_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(False)
        combo.setMaxVisibleItems(14)
        return combo

    def _account_options(self) -> list[tuple[str, str]]:
        # Real bill assignments always store the stable Production Plaid
        # account_id. Sandbox account IDs are never offered here.
        accounts = self.database.list_bank_accounts("production")

        options: list[tuple[str, str]] = []
        for account in accounts:
            # Payment Account describes where the bill payment leaves from, so
            # credit/loan accounts are not selectable here.
            if str(account["account_type"] or "") != "depository":
                continue

            account_id = str(account["plaid_account_id"])
            name = str(account["name"] or "(unnamed account)")
            mask = str(account["mask"] or "")
            display = name + (f" ••••{mask}" if mask else "")

            if int(account["is_bills_checking"] or 0):
                display = f"Bills Checking — {display}"

            options.append((account_id, display))

        return options

    def _refresh_account_choices(self) -> None:
        current_payment = self._combo_value(self.payment_account)
        payment_options = self._account_options()
        production_connected = bool(payment_options)

        self.payment_account.blockSignals(True)
        self.payment_account.clear()

        if production_connected:
            self.payment_account.addItem("Not set", None)
            for value, display in payment_options:
                self.payment_account.addItem(display, value)
            if current_payment:
                self._set_combo_value(
                    self.payment_account,
                    current_payment,
                )
            else:
                self.payment_account.setCurrentIndex(0)
        else:
            if current_payment:
                self.payment_account.addItem(
                    f"Unavailable linked account ({current_payment})",
                    current_payment,
                )
            else:
                self.payment_account.addItem(
                    "Connect your bank in Settings to assign accounts",
                    None,
                )
            self.payment_account.setCurrentIndex(0)

        self.payment_account.blockSignals(False)
        self.payment_account.setEnabled(production_connected)
        self._update_payment_account_help()

    def _update_payment_account_help(self) -> None:
        if not self._account_options():
            self.account_help.setText(
                "Connect your bank accounts before assigning a Payment Account."
            )
            return

        payment_account_id = self._combo_value(self.payment_account)
        bills = next(
            (
                row
                for row in self.database.list_bank_accounts("production")
                if int(row["is_bills_checking"] or 0)
            ),
            None,
        )

        if (
            bills is not None
            and payment_account_id
            == str(bills["plaid_account_id"])
        ):
            source = self.database.default_transfer_source_account(
                "production"
            )
            if source is None:
                self.account_help.setText(
                    "Bills Checking selected: choose a Default Transfer Source "
                    "checking account in Settings before saving this bill."
                )
                return
            source_label = str(source["name"] or "checking account")
            if source["mask"]:
                source_label += f" ••••{source['mask']}"
            self.account_help.setText(
                "Bills Checking selected: Transfer Required is automatically Yes "
                f"and Transfer Source is automatically {source_label}."
            )
        else:
            self.account_help.setText(
                "This bill will not be included in the Bills Checking transfer total."
            )

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str | None) -> None:
        if not value:
            combo.setCurrentIndex(0)
            return

        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

        # Preserve a stable account ID even if Plaid no longer returns that
        # account in the current account list.
        combo.addItem(f"Unavailable linked account ({value})", value)
        combo.setCurrentIndex(combo.count() - 1)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str | None:
        data = combo.currentData()
        if data not in (None, ""):
            return str(data).strip() or None

        text = combo.currentText().strip()
        if not text or text == "Not set":
            return None
        return text

    def refresh(self) -> None:
        self._refresh_account_choices()
        bills = self.database.list_bills(active_only=False)
        sorting = begin_table_refresh(self.bill_table)
        self.bill_table.setRowCount(len(bills))
        for index, bill in enumerate(bills):
            values = [
                (bill["name"], str(bill["name"]).casefold()),
                (bill["cycle"], str(bill["cycle"])),
                (bill["default_method"] or "—", str(bill["default_method"] or "").casefold()),
                ("Yes" if bill["active"] else "No", int(bool(bill["active"]))),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                    user_data=int(bill["id"]),
                )
                self.bill_table.setItem(index, col, item)
        end_table_refresh(self.bill_table, sorting)

        if not bills and self.selected_id is None:
            self._new_bill()

    def _load_selected(self) -> None:
        items = self.bill_table.selectedItems()
        if not items:
            return
        bill_id = int(items[0].data(Qt.ItemDataRole.UserRole))
        bill = next(
            (row for row in self.database.list_bills(active_only=False) if int(row["id"]) == bill_id),
            None,
        )
        if bill is None:
            return

        self.selected_id = bill_id
        self.name.setText(bill["name"])
        self.cycle.setCurrentText(bill["cycle"])
        self.latest_due.setText(bill["latest_due"] or "")
        self.method.setText(bill["default_method"] or "")
        self.aliases.setText(
            ", ".join(self.database.list_bill_aliases(bill_id))
        )
        self._set_combo_value(
            self.payment_account,
            bill["payment_account_id"],
        )
        self._update_payment_account_help()
        self.notes.setPlainText(bill["notes"] or "")
        self.status.setText("Loaded. Changes are not saved until you click Save.")

    def _new_bill(self) -> None:
        self.selected_id = None
        self.name.clear()
        self.cycle.setCurrentText("1st")
        self.latest_due.clear()
        self.method.clear()
        self.aliases.clear()
        self.payment_account.setCurrentIndex(0)
        self._update_payment_account_help()
        self.notes.clear()
        self.status.setText("New bill. Fill in the fields and click Save.")
        self.name.setFocus()

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.status.setText("Bill name is required.")
            return

        try:
            self.backup_manager.create_backup("pre-bill-save")
            payment_account_id = self._combo_value(
                self.payment_account
            )
            bill_id = self.database.upsert_bill(
                name=name,
                cycle=self.cycle.currentText(),
                latest_due=self.latest_due.text().strip() or None,
                default_method=self.method.text().strip() or None,
                payment_account_id=payment_account_id,
                active=True,
                notes=self.notes.toPlainText().strip() or None,
            )
            self.database.set_bill_payment_account(
                bill_id,
                payment_account_id,
            )
            self.database.set_bill_aliases(
                bill_id,
                [
                    alias
                    for alias in self.aliases.text().split(",")
                    if alias.strip()
                ],
            )
            today = date.today()
            self.database.materialize_active_bills(
                today.year,
                today.month,
                today=today,
            )
        except Exception as exc:
            self.status.setText(f"Save blocked: {exc}")
            return

        self.selected_id = bill_id
        self.status.setText("Saved.")
        self.refresh()
        self.on_changed()

    def _deactivate(self) -> None:
        if self.selected_id is None:
            self.status.setText("Select an existing bill first.")
            return
        try:
            self.backup_manager.create_backup("pre-bill-deactivate")
        except Exception as exc:
            self.status.setText(f"Backup failed; bill was not changed: {exc}")
            return
        self.database.set_bill_active(self.selected_id, False)
        self.status.setText("Bill marked inactive. Historical rows were not deleted.")
        self.refresh()
        self.on_changed()


class MainWindow(QMainWindow):
    def __init__(self, database: Database, backup_manager: BackupManager):
        super().__init__()
        self.database = database
        self.backup_manager = backup_manager
        self.setWindowTitle("Bi-Weekly Bills")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 22, 18, 18)
        side.setSpacing(8)

        brand = QLabel()
        brand.setObjectName("BrandLogo")
        brand.setAccessibleName("Bi-Weekly Bills")
        brand_pixmap = brand_logo_pixmap(184)
        brand.setPixmap(brand_pixmap)
        brand.setFixedSize(brand_pixmap.size())
        brand.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        brand_sub = QLabel("1st / 15th household workflow")
        brand_sub.setObjectName("BrandSub")
        side.addWidget(brand)
        side.addWidget(brand_sub)
        side.addSpacing(18)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(28, 16, 28, 16)

        heading_box = QVBoxLayout()
        self.page_title = QLabel("Overview")
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel("Your 1st/15th bill workflow at a glance")
        self.page_subtitle.setObjectName("PageSubtitle")
        heading_box.addWidget(self.page_title)
        heading_box.addWidget(self.page_subtitle)
        topbar_layout.addLayout(heading_box)
        topbar_layout.addStretch(1)


        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(
            database,
            auto_refresh=False,
        )
        self.bills = BillsPage(
            database,
            self._refresh_dashboard,
            backup_manager,
            auto_refresh=False,
        )
        self.pages.addWidget(self.dashboard)
        self.pages.addWidget(self.bills)
        self.pay_periods = PayPeriodsPage(
            database,
            self._refresh_dashboard,
            backup_manager,
            auto_refresh=False,
        )
        self.pages.addWidget(self.pay_periods)
        self.dashboard.open_pay_period.connect(self._open_pay_period)
        self.transactions = TransactionsPage(
            database,
            self._refresh_reconciliation_pages,
            backup_manager,
            auto_refresh=False,
        )
        self.pages.addWidget(self.transactions)
        self.reconciliation = ReconciliationPage(
            database,
            self._refresh_reconciliation_pages,
            backup_manager,
            auto_refresh=False,
        )
        self.pages.addWidget(self.reconciliation)
        self.reports = ReportsPage(
            database,
            auto_refresh=False,
        )
        self.pages.addWidget(self.reports)
        self.settings = SettingsPage(
            database,
            backup_manager,
            self._refresh_all_after_restore,
            self._refresh_bank_pages,
            auto_refresh=False,
        )
        self.pages.addWidget(self.settings)

        self._nav_defaults = {
            "overview": (
                0,
                "Overview",
                "Your 1st/15th bill workflow at a glance",
            ),
            "bills": (
                1,
                "Bills",
                "Recurring bills, accounts, and merchant aliases",
            ),
            "pay_periods": (
                2,
                "Pay Periods",
                "Pay bills and edit each period inline",
            ),
            "transactions": (
                3,
                "Transactions",
                "Bank activity, matches, and review",
            ),
            "reconciliation": (
                4,
                "Reconciliation",
                "Historical audit and integrity diagnostics",
            ),
            "reports": (
                5,
                "Reports",
                "ODS, PDF, and Excel exports",
            ),
            "settings": (
                6,
                "Settings",
                "Bank setup, connected accounts, and backups",
            ),
        }
        preferences = load_user_ui_preferences()
        saved_order = preferences.get("sidebar_order", [])
        saved_labels = preferences.get("sidebar_labels", {})

        order: list[str] = []
        if isinstance(saved_order, list):
            for raw_key in saved_order:
                key = str(raw_key)
                if key in self._nav_defaults and key not in order:
                    order.append(key)
        for key in self._nav_defaults:
            if key not in order:
                order.append(key)

        self._sidebar_edit_mode = False

        edit_row = QHBoxLayout()
        edit_row.setContentsMargins(0, 0, 0, 0)
        edit_row.addStretch(1)
        self.nav_edit_button = QPushButton("⚙")
        self.nav_edit_button.setObjectName("NavEditButton")
        self.nav_edit_button.setAccessibleName("Edit sidebar sections")
        self.nav_edit_button.setCheckable(True)
        self.nav_edit_button.setFixedSize(30, 30)
        self.nav_edit_button.toggled.connect(
            self._set_sidebar_edit_mode
        )
        edit_row.addWidget(self.nav_edit_button)
        side.addLayout(edit_row)

        self.nav_list = SidebarNavList()
        self.nav_list.setObjectName("NavList")
        self.nav_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.nav_list.setToolTip("")

        for key in order:
            page_index, default_label, _subtitle = self._nav_defaults[key]
            label = default_label
            if isinstance(saved_labels, dict):
                custom_label = str(saved_labels.get(key, "")).strip()
                if custom_label:
                    label = custom_label
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setToolTip("")
            item.setData(
                int(Qt.ItemDataRole.UserRole) + 1,
                page_index,
            )
            self.nav_list.addItem(item)

        self.nav_list.setFixedHeight(
            max(220, self.nav_list.count() * 46 + 8)
        )
        self.nav_list.currentItemChanged.connect(
            self._on_nav_current_item_changed
        )
        self.nav_list.itemChanged.connect(self._on_nav_item_changed)
        self.nav_list.orderChanged.connect(self._on_nav_rows_moved)
        side.addWidget(self.nav_list)

        # Compatibility for callers that only inspect the old nav_buttons list.
        self.nav_buttons = [
            self.nav_list.item(row)
            for row in range(self.nav_list.count())
        ]

        self.nav_hint = QLabel(
            "Edit mode · drag only from the ≡ grip\n"
            "Double-click/F2 to rename · auto-saves"
        )
        self.nav_hint.setObjectName("BrandSub")
        self.nav_hint.setWordWrap(True)
        self.nav_hint.setVisible(False)
        side.addWidget(self.nav_hint)

        side.addStretch(1)
        bills_account = next(
            (
                row
                for row in database.list_bank_accounts("production")
                if int(row["is_bills_checking"] or 0)
            ),
            None,
        )
        if bills_account is not None:
            mask = str(bills_account["mask"] or "")
            note_text = "Bills Checking"
            if mask:
                note_text += f"\n••••{mask}"
        else:
            note_text = "Bills Checking\nnot selected"

        # Pages load on first visit and stay cached until a write marks them dirty.
        self._page_dirty = {
            index: True
            for index in range(self.pages.count())
        }

        self.account_note = QLabel(note_text)
        self.account_note.setObjectName("BrandSub")
        self.account_note.setAlignment(Qt.AlignmentFlag.AlignLeft)
        side.addWidget(self.account_note)

        content_layout.addWidget(topbar)
        content_layout.addWidget(self.pages, 1)

        outer.addWidget(sidebar)
        outer.addWidget(content, 1)

        first_run_state = connection_state(database)
        if first_run_state.action == "sync":
            self._navigate(
                0,
                "Overview",
                "Your 1st/15th bill workflow at a glance",
            )
        else:
            self._navigate(
                6,
                "Settings",
                "Bank setup, connected accounts, and backups",
            )

    def _open_pay_period(self, cycle: str) -> None:
        self.pay_periods.set_view(cycle, refresh=False)
        self._page_dirty[2] = True
        self._navigate(
            2,
            "Pay Periods",
            "Pay bills and track where you left off",
        )

    def _set_sidebar_edit_mode(self, enabled: bool) -> None:
        self._sidebar_edit_mode = bool(enabled)
        self.nav_list.set_edit_mode(self._sidebar_edit_mode)
        self.nav_hint.setVisible(self._sidebar_edit_mode)

        if self._sidebar_edit_mode:
            self.nav_list.setToolTip(
                "Drag only from the ≡ grip. "
                "Double-click or press F2 to rename."
            )
            self.nav_edit_button.setToolTip(
                "Finish editing sidebar"
            )
        else:
            self.nav_list.setToolTip("")
            self.nav_edit_button.setToolTip("")

        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item is None:
                continue
            flags = (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            if self._sidebar_edit_mode:
                # Rows can be edited and dragged, but are deliberately not
                # drop targets. Reordering is insert-only between rows.
                flags |= (
                    Qt.ItemFlag.ItemIsEditable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                label = item.text().strip()
                item.setToolTip(
                    f"{label}\n"
                    "Drag from ≡ to move · Double-click/F2 to rename"
                )
            else:
                item.setToolTip("")
            item.setFlags(flags)

        self.nav_list.viewport().update()

    def _nav_item_for_page(self, page_index: int) -> QListWidgetItem | None:
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item is None:
                continue
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            details = self._nav_defaults.get(key)
            if details is not None and details[0] == page_index:
                return item
        return None

    def _on_nav_current_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        details = self._nav_defaults.get(key)
        if details is None:
            return
        page_index, _default_label, subtitle = details
        self._navigate(page_index, current.text().strip(), subtitle)

    def _on_nav_item_changed(self, item: QListWidgetItem) -> None:
        if not self._sidebar_edit_mode:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        details = self._nav_defaults.get(key)
        if details is None:
            return
        page_index, default_label, _subtitle = details
        label = item.text().strip()
        if not label:
            label = default_label
        if label != item.text():
            self.nav_list.blockSignals(True)
            item.setText(label)
            self.nav_list.blockSignals(False)
        item.setToolTip(
            f"{label}\n"
            "Drag from ≡ to move · Double-click/F2 to rename"
        )
        if self.pages.currentIndex() == page_index:
            self.page_title.setText(label)
        self._save_nav_preferences()

    def _on_nav_rows_moved(self, *_args) -> None:
        if not self._sidebar_edit_mode:
            return
        self.nav_buttons = [
            self.nav_list.item(row)
            for row in range(self.nav_list.count())
        ]
        self._save_nav_preferences()

    def _save_nav_preferences(self) -> None:
        order: list[str] = []
        labels: dict[str, str] = {}
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item is None:
                continue
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            details = self._nav_defaults.get(key)
            if details is None:
                continue
            _page_index, default_label, _subtitle = details
            label = item.text().strip() or default_label
            order.append(key)
            if label != default_label:
                labels[key] = label

        try:
            save_user_ui_preferences(
                sidebar_order=order,
                sidebar_labels=labels,
            )
        except Exception as exc:
            self.nav_hint.setText(
                f"Sidebar preference save failed: {exc}"
            )
            return

        self.nav_hint.setText(
            "Edit mode · drag only from the ≡ grip\n"
            "Double-click/F2 to rename · auto-saves"
        )

    def _refresh_sidebar_account_note(self) -> None:
        bills_account = next(
            (
                row
                for row in self.database.list_bank_accounts("production")
                if int(row["is_bills_checking"] or 0)
            ),
            None,
        )
        if bills_account is None:
            self.account_note.setText("Bills Checking\nnot selected")
            return
        mask = str(bills_account["mask"] or "")
        text = "Bills Checking"
        if mask:
            text += f"\n••••{mask}"
        self.account_note.setText(text)

    def _mark_pages_dirty(
        self,
        indices: tuple[int, ...],
        *,
        keep_current_clean: bool = True,
    ) -> None:
        current = self.pages.currentIndex()
        for index in indices:
            if keep_current_clean and index == current:
                continue
            self._page_dirty[index] = True

    def _refresh_dashboard(self) -> None:
        # A bill/checklist edit can affect summaries, funding, matching,
        # reconciliation audit, and reports. The writing page refreshes itself.
        self._refresh_sidebar_account_note()
        self._mark_pages_dirty((0, 1, 2, 3, 4, 5))

    def _refresh_bank_pages(self) -> None:
        # Bank sync/account-role changes affect every data page.
        self._refresh_sidebar_account_note()
        self._mark_pages_dirty((0, 1, 2, 3, 4, 5))

    def _refresh_reconciliation_pages(self) -> None:
        # Transactions/Reconciliation refresh themselves after their action.
        self._mark_pages_dirty((0, 2, 3, 4, 5))

    def _refresh_all_after_restore(self) -> None:
        self._refresh_sidebar_account_note()
        self._mark_pages_dirty(
            tuple(range(self.pages.count())),
            keep_current_clean=True,
        )
        if self.pages.currentIndex() == 6:
            self.settings.refresh()
            self._page_dirty[6] = False

    def _refresh_page(self, index: int) -> None:
        if index == 0:
            self.dashboard.refresh()
        elif index == 1:
            self.bills.refresh()
        elif index == 2:
            self.pay_periods.refresh()
        elif index == 3:
            self.transactions.refresh()
        elif index == 4:
            self.reconciliation.refresh()
        elif index == 5:
            self.reports.refresh()
        elif index == 6:
            self.settings.refresh()

    def _navigate(self, index: int, title: str, subtitle: str) -> None:
        self.pages.setCurrentIndex(index)
        item = self._nav_item_for_page(index)
        resolved_title = (
            item.text().strip()
            if item is not None and item.text().strip()
            else title
        )
        self.page_title.setText(resolved_title)
        self.page_subtitle.setText(subtitle)

        if item is not None and self.nav_list.currentItem() is not item:
            self.nav_list.blockSignals(True)
            self.nav_list.setCurrentItem(item)
            self.nav_list.blockSignals(False)

        if self._page_dirty.get(index, True):
            self._refresh_page(index)
            self._page_dirty[index] = False
