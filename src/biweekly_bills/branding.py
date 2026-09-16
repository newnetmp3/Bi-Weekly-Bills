from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .desktop_integration import APP_ID, APP_NAME, ICON_RESOURCE, LOGO_RESOURCE


def _svg_pixmap(relative_path: str, width: int) -> QPixmap:
    data = files("biweekly_bills").joinpath(relative_path).read_bytes()
    renderer = QSvgRenderer(QByteArray(data))
    default = renderer.defaultSize()
    if not renderer.isValid() or default.width() <= 0 or default.height() <= 0:
        return QPixmap()

    height = max(1, round(width * default.height() / default.width()))
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def application_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512):
        pixmap = _svg_pixmap(ICON_RESOURCE, size)
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon


def brand_logo_pixmap(width: int = 188) -> QPixmap:
    return _svg_pixmap(LOGO_RESOURCE, width)
