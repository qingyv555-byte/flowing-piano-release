from __future__ import annotations

from PySide6.QtCore import QRect


DEFAULT_MIN_WIDTH = 1000
DEFAULT_MIN_HEIGHT = 640
DEFAULT_WINDOW_SCALE = 0.80


def default_geometry(work_area: QRect) -> QRect:
    """Choose a useful first-run window rectangle inside a screen work area."""
    area = QRect(work_area)
    if not area.isValid() or area.width() <= 0 or area.height() <= 0:
        return QRect(0, 0, DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT)

    width = min(
        area.width(), max(DEFAULT_MIN_WIDTH, round(area.width() * DEFAULT_WINDOW_SCALE))
    )
    height = min(
        area.height(), max(DEFAULT_MIN_HEIGHT, round(area.height() * DEFAULT_WINDOW_SCALE))
    )
    x = area.left() + max(0, (area.width() - width) // 2)
    y = area.top() + max(0, (area.height() - height) // 2)
    return QRect(x, y, width, height)


def clamp_geometry(geometry: QRect, work_area: QRect) -> QRect:
    """Keep a restored window visible and no larger than the current work area."""
    area = QRect(work_area)
    if not area.isValid() or area.width() <= 0 or area.height() <= 0:
        return QRect(geometry)

    width = min(max(1, geometry.width()), area.width())
    height = min(max(1, geometry.height()), area.height())
    x = min(max(geometry.left(), area.left()), area.right() - width + 1)
    y = min(max(geometry.top(), area.top()), area.bottom() - height + 1)
    result = QRect(x, y, width, height)
    if not result.intersects(area):
        result.moveCenter(area.center())
    return result
