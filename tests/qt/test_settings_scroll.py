"""Exercise physical-wheel and touchpad scrolling in the actual settings popup."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest
import pytest
import time

from tests.qt.test_studio_appearance import appearance, appearance_window, png
from tests.qt.test_settings_interactions import open_appearance
from tests.qt.test_studio_workflows import find_item, qml


def wheel(root, scroll, angle=-120, pixels=0):
    point = scroll.mapToScene(QPointF(scroll.width() - 35, 70))
    event = QWheelEvent(point, point, QPoint(0, pixels), QPoint(0, angle),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    event.setTimestamp(int(time.monotonic() * 1000))
    QGuiApplication.sendEvent(root, event)


def position(scroll):
    return float(qml(scroll, 'contentItem.contentY'))


def test_settings_wheel_moves_through_intermediate_positions(appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    scroll = find_item(root, 'studioAppearanceScroll')
    qml(scroll, 'contentItem.contentY = 0; true')
    wheel(root, scroll)
    samples = [position(scroll)]
    for _ in range(16):
        QTest.qWait(15)
        samples.append(position(scroll))
    assert samples[-1] > 10
    assert len({round(value, 1) for value in samples}) >= 5, samples
    assert all(b >= a - .1 for a, b in zip(samples, samples[1:])), samples
    dialog.close()


@pytest.mark.parametrize('delta', [-1, -15, -120])
def test_settings_continuous_wheel_does_not_stall_between_events(appearance_window, qtbot, delta, tmp_path):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    settings.importBackground('editor', str(png(tmp_path / 'scroll-background.png', 2560, 1440)))
    qtbot.waitUntil(lambda: not settings.importBusy)
    root.grabWindow()
    scroll = find_item(root, 'studioAppearanceScroll')
    qml(scroll, 'contentItem.contentY = 0; true')
    samples = []
    for _ in range(5):
        wheel(root, scroll, angle=delta)
        for _ in range(4):
            QTest.qWait(15)
            samples.append(position(scroll))
    assert samples[-1] > (1 if delta == -1 else 20)
    if delta == -1:
        # Fine input must advance between logical pixels, rather than freezing
        # until another whole pixel accumulates (visible at 150% Windows DPI).
        assert sum(abs(value - round(value)) > .01 for value in samples) >= 8, samples
    assert all(b >= a - .1 for a, b in zip(samples, samples[1:])), samples
    dialog.close()


def test_settings_wheel_stops_at_edge_without_bounce(appearance_window, qtbot):
    root, dialog, settings = open_appearance(appearance_window, qtbot, 0)
    scroll = find_item(root, 'studioAppearanceScroll')
    limit = float(qml(scroll, 'contentItem.contentHeight - contentItem.height'))
    qml(scroll, f'contentItem.contentY = {limit - 5}; true')
    samples = []
    for _ in range(5):
        wheel(root, scroll)
        QTest.qWait(30)
        samples.append(position(scroll))
    QTest.qWait(300)
    samples.append(position(scroll))
    assert all(0 <= value <= limit + .01 for value in samples), samples
    assert samples[-1] == pytest.approx(limit, abs=.01)
    wheel(root, scroll, angle=120)
    qtbot.waitUntil(lambda: position(scroll) < limit - 10)
    dialog.close()
