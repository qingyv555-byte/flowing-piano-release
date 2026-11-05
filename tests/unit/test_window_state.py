from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QWindow

import ai_piano_studio.window_state as window_state


class FakeWindow:
    def __init__(self, geometry=QRect(10, 20, 1200, 700), visibility=Qt.WindowState.WindowNoState):
        self._geometry = QRect(geometry)
        self._visibility = visibility
        self.show_calls = []

    def geometry(self):
        return QRect(self._geometry)

    def setGeometry(self, geometry):
        self._geometry = QRect(geometry)

    def visibility(self):
        return self._visibility

    def showMaximized(self):
        self.show_calls.append("maximized")

    def showNormal(self):
        self.show_calls.append("normal")


def test_save_and_restore_main_window_state(qapp, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    area = QRect(0, 0, 1600, 900)
    monkeypatch.setattr(window_state, "_screen_work_area", lambda: area)
    window = FakeWindow(QRect(100, 80, 1300, 760))

    window_state.save_main_window(window, settings)
    restored = FakeWindow(QRect(), visibility=Qt.WindowState.WindowNoState)
    geometry = window_state.restore_main_window(restored, settings)

    assert geometry == QRect(100, 80, 1300, 760)
    assert restored.geometry() == geometry
    assert restored.show_calls == ["normal"]


def test_maximized_save_preserves_previous_normal_geometry(qapp, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(window_state, "_screen_work_area", lambda: QRect(0, 0, 1600, 900))
    normal = FakeWindow(QRect(100, 80, 1300, 760))
    window_state.save_main_window(normal, settings)

    maximized = FakeWindow(QRect(0, 0, 1600, 900), QWindow.Visibility.Maximized)
    window_state.save_main_window(maximized, settings)
    restored = FakeWindow(QRect())
    window_state.restore_main_window(restored, settings)

    assert restored.geometry() == QRect(100, 80, 1300, 760)
    assert restored.show_calls == ["maximized"]
    assert settings.value("mainWindow/maximized", type=bool) is True


def test_restore_expands_legacy_small_window_to_show_the_full_editor(
    qapp, monkeypatch, tmp_path
):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    area = QRect(0, 0, 2048, 1120)
    monkeypatch.setattr(window_state, "_screen_work_area", lambda: area)
    settings.setValue("mainWindow/x", 120)
    settings.setValue("mainWindow/y", 90)
    settings.setValue("mainWindow/width", 1113)
    settings.setValue("mainWindow/height", 800)
    settings.setValue("mainWindow/maximized", False)
    restored = FakeWindow(QRect())

    geometry = window_state.restore_main_window(restored, settings)

    assert geometry == window_state.default_geometry(area)
    assert restored.geometry() == geometry
    assert restored.show_calls == ["normal"]


def test_restore_migrates_old_default_window_to_compact_default(
    qapp, monkeypatch, tmp_path
):
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    area = QRect(0, 0, 2560, 1440)
    monkeypatch.setattr(window_state, "_screen_work_area", lambda: area)
    settings.setValue("mainWindow/x", 32)
    settings.setValue("mainWindow/y", 24)
    settings.setValue("mainWindow/width", 2496)
    settings.setValue("mainWindow/height", 1344)
    settings.setValue("mainWindow/maximized", False)
    restored = FakeWindow(QRect())

    geometry = window_state.restore_main_window(restored, settings)

    assert geometry == window_state.default_geometry(area)
    assert settings.value("mainWindow/geometryVersion", type=int) == 2
