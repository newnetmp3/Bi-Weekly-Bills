from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QProgressBar,
    QStyle,
    QStyleOptionProgressBar,
    QStylePainter,
)


class AdaptiveTextProgressBar(QProgressBar):
    """Progress bar whose centered text changes contrast at the fill boundary."""

    light_text = QColor("#edf1f7")
    dark_text = QColor("#0b1020")

    def fill_rect(self) -> QRect:
        """Return the approximate painted chunk rect used for text clipping."""
        content = self.rect().adjusted(1, 1, -1, -1)
        span = self.maximum() - self.minimum()
        if span <= 0 or content.width() <= 0:
            return QRect()

        ratio = (self.value() - self.minimum()) / span
        ratio = max(0.0, min(1.0, ratio))
        width = int(round(content.width() * ratio))
        if width <= 0:
            return QRect()

        fill_from_right = (
            self.invertedAppearance()
            ^ (self.layoutDirection() == Qt.LayoutDirection.RightToLeft)
        )
        if fill_from_right:
            return QRect(
                content.right() - width + 1,
                content.top(),
                width,
                content.height(),
            )
        return QRect(
            content.left(),
            content.top(),
            width,
            content.height(),
        )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QStylePainter(self)
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)

        text = option.text if option.textVisible else ""
        option.textVisible = False
        painter.drawControl(QStyle.ControlElement.CE_ProgressBar, option)

        if not text:
            return

        text_rect = self.rect()
        flags = (
            Qt.AlignmentFlag.AlignCenter
            | Qt.TextFlag.TextSingleLine
        )
        filled = self.fill_rect()

        painter.save()
        painter.setPen(self.light_text)
        if filled.isValid():
            unfilled = self.rect()
            if filled.left() <= unfilled.left():
                unfilled.setLeft(filled.right() + 1)
            else:
                unfilled.setRight(filled.left() - 1)
            painter.setClipRect(unfilled)
        painter.drawText(text_rect, flags, text)
        painter.restore()

        if filled.isValid():
            painter.save()
            painter.setPen(self.dark_text)
            painter.setClipRect(filled)
            painter.drawText(text_rect, flags, text)
            painter.restore()
