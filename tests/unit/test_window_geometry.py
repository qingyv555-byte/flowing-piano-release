from PySide6.QtCore import QRect

from ai_piano_studio.window_geometry import default_geometry, clamp_geometry


def test_default_geometry_uses_available_work_area_without_fixed_size():
    geometry = default_geometry(QRect(0, 0, 1366, 768))

    assert geometry.width() == 1093
    assert geometry.height() >= 640
    assert geometry.x() == 136
    assert geometry.y() == 64
    assert geometry.right() < 1366
    assert geometry.bottom() < 768


def test_default_geometry_scales_down_for_small_work_area():
    geometry = default_geometry(QRect(0, 0, 1024, 640))

    assert geometry.width() <= 1024
    assert geometry.height() <= 640
    assert geometry.width() >= 1000
    assert geometry.height() >= 640


def test_clamp_geometry_recovers_fully_offscreen_window():
    work_area = QRect(100, 50, 1600, 900)
    restored = QRect(-1800, -1200, 1200, 700)

    geometry = clamp_geometry(restored, work_area)

    assert geometry.width() == restored.width()
    assert geometry.height() == restored.height()
    assert geometry.intersects(work_area)
    assert geometry.left() >= work_area.left()
    assert geometry.top() >= work_area.top()


def test_clamp_geometry_shrinks_window_larger_than_work_area():
    work_area = QRect(0, 0, 1000, 640)
    restored = QRect(20, 30, 1600, 900)

    geometry = clamp_geometry(restored, work_area)

    assert geometry == work_area
