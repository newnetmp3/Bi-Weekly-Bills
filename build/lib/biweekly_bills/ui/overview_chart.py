from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DonutBreakdownChart(QWidget):
    """Compact two-part donut chart designed for dashboard metric cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._first_label = "First"
        self._first_value = 0
        self._second_label = "Second"
        self._second_value = 0
        self._center_text = "—"
        self._center_subtext = ""
        self.setMinimumHeight(92)
        self.setMaximumHeight(108)

    def sizeHint(self) -> QSize:
        return QSize(300, 100)

    def set_data(
        self,
        first_label: str,
        first_value: int,
        second_label: str,
        second_value: int,
        *,
        center_text: str,
        center_subtext: str = "",
    ) -> None:
        self._first_label = first_label
        self._first_value = max(int(first_value), 0)
        self._second_label = second_label
        self._second_value = max(int(second_value), 0)
        self._center_text = center_text
        self._center_subtext = center_subtext
        self.setAccessibleName(
            f"{first_label}: {self._first_value}; "
            f"{second_label}: {self._second_value}; "
            f"{center_text} {center_subtext}".strip()
        )
        self.update()

    @staticmethod
    def _money(cents: int) -> str:
        return f"${cents / 100:,.2f}"

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = self.width()
        height = self.height()
        diameter = min(78.0, max(58.0, float(height - 16)))
        donut = QRectF(8.0, (height - diameter) / 2.0, diameter, diameter)

        track = QColor("#26324a")
        first_color = QColor("#c8ff3d")
        second_color = QColor("#5d8cff")
        text_color = QColor("#edf1f7")
        muted = QColor("#8998b3")

        pen = QPen(track, 10.0)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawArc(donut, 0, 360 * 16)

        total = self._first_value + self._second_value
        if total > 0:
            first_span = int(round(360.0 * self._first_value / total * 16))
            second_span = 360 * 16 - first_span

            first_pen = QPen(first_color, 10.0)
            first_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(first_pen)
            painter.drawArc(donut, 90 * 16, -first_span)

            if second_span > 0:
                second_pen = QPen(second_color, 10.0)
                second_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(second_pen)
                painter.drawArc(
                    donut,
                    (90 * 16) - first_span,
                    -second_span,
                )

        center_font = QFont(self.font())
        center_font.setBold(True)
        center_font.setPointSizeF(max(8.5, self.font().pointSizeF()))
        painter.setFont(center_font)
        painter.setPen(text_color)
        center_rect = donut.adjusted(10, 13, -10, -22)
        painter.drawText(
            center_rect,
            Qt.AlignmentFlag.AlignCenter,
            self._center_text,
        )

        if self._center_subtext:
            sub_font = QFont(self.font())
            sub_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.0))
            painter.setFont(sub_font)
            painter.setPen(muted)
            sub_rect = donut.adjusted(8, 35, -8, -8)
            painter.drawText(
                sub_rect,
                Qt.AlignmentFlag.AlignCenter,
                self._center_subtext,
            )

        legend_x = donut.right() + 18.0
        legend_width = max(40.0, width - legend_x - 8.0)
        legend_font = QFont(self.font())
        legend_font.setPointSizeF(max(7.5, self.font().pointSizeF() - 0.5))
        painter.setFont(legend_font)

        entries = (
            (self._first_label, self._first_value, first_color),
            (self._second_label, self._second_value, second_color),
        )
        start_y = max(21.0, (height - 48.0) / 2.0 + 8.0)
        for index, (label, value, color) in enumerate(entries):
            y = start_y + (index * 31.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(legend_x, y - 7.0, 8.0, 8.0))

            painter.setPen(muted)
            painter.drawText(
                QRectF(legend_x + 14.0, y - 11.0, legend_width, 15.0),
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

            value_font = QFont(legend_font)
            value_font.setBold(True)
            painter.setFont(value_font)
            painter.setPen(text_color)
            painter.drawText(
                QRectF(legend_x + 14.0, y + 3.0, legend_width, 16.0),
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter,
                self._money(value),
            )
            painter.setFont(legend_font)
