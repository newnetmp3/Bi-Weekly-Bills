from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu, QTableWidget, QWidget


def copy_text(text: str) -> None:
    QApplication.clipboard().setText(str(text))


def row_text(
    table: QTableWidget,
    row: int,
    *,
    separator: str = "\t",
) -> str:
    values: list[str] = []
    for column in range(table.columnCount()):
        if table.isColumnHidden(column):
            continue
        item = table.item(row, column)
        values.append("" if item is None else item.text())
    return separator.join(values)


def begin_table_context_menu(
    table: QTableWidget,
    position: QPoint,
    *,
    parent: QWidget | None = None,
) -> tuple[QMenu, int, int] | None:
    item = table.itemAt(position)
    if item is None:
        return None

    row = item.row()
    column = item.column()
    table.selectRow(row)

    menu = QMenu(parent or table)
    copy_cell = menu.addAction("Copy cell")
    copy_cell.triggered.connect(
        lambda checked=False, value=item.text(): copy_text(value)
    )
    copy_row = menu.addAction("Copy row")
    copy_row.triggered.connect(
        lambda checked=False, selected_row=row: copy_text(
            row_text(table, selected_row)
        )
    )
    menu.addSeparator()
    return menu, row, column


def show_table_context_menu(
    table: QTableWidget,
    menu: QMenu,
    position: QPoint,
) -> None:
    menu.exec(table.viewport().mapToGlobal(position))
