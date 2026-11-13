from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem


SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
QT_INT64_MIN = -(2**63)
QT_INT64_MAX = 2**63 - 1


def qt_safe_sort_value(value: Any) -> Any:
    """Return a value that can be safely marshalled through Qt/QVariant."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < QT_INT64_MIN:
            return QT_INT64_MIN
        if value > QT_INT64_MAX:
            return QT_INT64_MAX
    return value


class SortableTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem with an explicit sort key independent of display text."""

    def __init__(
        self,
        text: str,
        *,
        sort_value: Any | None = None,
        user_data: Any | None = None,
    ):
        super().__init__(text)
        if sort_value is not None:
            self.setData(SORT_ROLE, qt_safe_sort_value(sort_value))
        if user_data is not None:
            self.setData(Qt.ItemDataRole.UserRole, user_data)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)

        if left is not None and right is not None:
            try:
                return left < right
            except TypeError:
                return str(left).casefold() < str(right).casefold()

        return super().__lt__(other)


def set_sortable(table: QTableWidget) -> None:
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    header.setSortIndicatorShown(True)
    table.setSortingEnabled(True)


def configure_resizable_columns(
    table: QTableWidget,
    widths: tuple[int, ...] | list[int] | None = None,
    *,
    minimum: int = 64,
) -> None:
    """Make every column user-resizable with practical starting widths.

    Interactive mode is deliberate: Stretch and ResizeToContents continually
    reclaim control from the user and make long values difficult to inspect.
    Once set, widths survive normal table refreshes/navigation for the lifetime
    of the page.
    """
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(int(minimum))
    header.setStretchLastSection(False)
    header.setSectionsMovable(False)

    if widths is None:
        return
    for column, width in enumerate(widths):
        if column >= table.columnCount():
            break
        table.setColumnWidth(column, max(int(width), int(minimum)))


def begin_table_refresh(table: QTableWidget) -> bool:
    enabled = table.isSortingEnabled()
    if enabled:
        table.setSortingEnabled(False)
    return enabled


def end_table_refresh(table: QTableWidget, was_enabled: bool) -> None:
    if was_enabled:
        table.setSortingEnabled(True)
