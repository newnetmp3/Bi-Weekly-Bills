from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


_busy_depth = 0


def begin_busy_cursor() -> None:
    """Show the application busy cursor, supporting nested background workers."""
    global _busy_depth
    if _busy_depth == 0:
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
    _busy_depth += 1


def end_busy_cursor() -> None:
    """Release one busy-cursor claim and restore normal cursor at depth zero."""
    global _busy_depth
    if _busy_depth <= 0:
        _busy_depth = 0
        return

    _busy_depth -= 1
    if _busy_depth == 0:
        QApplication.restoreOverrideCursor()
