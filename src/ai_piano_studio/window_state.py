from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QGuiApplication, QWindow

from ai_piano_studio.window_geometry import clamp_geometry, default_geometry


_SETTINGS_GROUP = "mainWindow"
_GEOMETRY_VERSION_KEY = "geometryVersion"
_DEFAULT_GEOMETRY_VERSION = 2


def _screen_work_area() -> QRect:
    screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect()


def _saved_geometry(settings: QSettings) -> QRect | None:
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        values = [settings.value(key, None) for key in ("x", "y", "width", "height")]
    finally:
        settings.endGroup()
    if any(value is None for value in values):
        return None
    try:
        x, y, width, height = (int(value) for value in values)
    except (TypeError, ValueError):
        return None
    geometry = QRect(x, y, width, height)
    return geometry if geometry.isValid() else None


def _is_maximized(window: Any) -> bool:
    visibility = window.visibility() if callable(getattr(window, "visibility", None)) else None
    return visibility == QWindow.Visibility.Maximized


def restore_main_window(window: Any, settings: QSettings) -> QRect:
    """Restore a main window and keep it visible after monitor changes."""
    work_area = _screen_work_area()
    saved_geometry = _saved_geometry(settings)
    geometry = saved_geometry or default_geometry(work_area)
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        try:
            geometry_version = int(settings.value(_GEOMETRY_VERSION_KEY, 0))
        except (TypeError, ValueError):
            geometry_version = 0
        if saved_geometry is not None and geometry_version < _DEFAULT_GEOMETRY_VERSION:
            # The previous release opened almost edge-to-edge, which hides the
            # selection action rail on common laptop work areas. Apply the new
            # compact default once; subsequent user-resized rectangles win.
            geometry = default_geometry(work_area)
            settings.setValue(_GEOMETRY_VERSION_KEY, _DEFAULT_GEOMETRY_VERSION)
    finally:
        settings.endGroup()
    if (
        saved_geometry is not None
        and work_area.isValid()
        and (
            saved_geometry.width() < work_area.width() * 0.75
            or saved_geometry.height() < work_area.height() * 0.70
        )
    ):
        # Older builds persisted a compact window that hides primary editor
        # actions behind overflow menus. Upgrade only clearly undersized
        # rectangles; sufficiently large user-resized windows remain intact.
        geometry = default_geometry(work_area)
    geometry = clamp_geometry(geometry, work_area)
    window.setGeometry(geometry)
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        maximized = settings.value("maximized", False, type=bool)
    finally:
        settings.endGroup()
    if maximized:
        window.showMaximized()
    else:
        window.showNormal()
    return geometry


def save_main_window(window: Any, settings: QSettings) -> None:
    """Persist normal geometry and maximized state for a QQuickWindow."""
    is_maximized = _is_maximized(window)
    geometry = window.geometry()
    if is_maximized:
        geometry = _saved_geometry(settings) or geometry
    settings.beginGroup(_SETTINGS_GROUP)
    try:
        settings.setValue("x", geometry.x())
        settings.setValue("y", geometry.y())
        settings.setValue("width", geometry.width())
        settings.setValue("height", geometry.height())
        settings.setValue("maximized", is_maximized)
        settings.setValue(_GEOMETRY_VERSION_KEY, _DEFAULT_GEOMETRY_VERSION)
        settings.sync()
    finally:
        settings.endGroup()
