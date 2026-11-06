import pytest
from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer
from PySide6.QtTest import QTest

from tests.qt.test_playback_canvas_cache import memory_audio_only, prepare
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session, find_visual_item


def test_viewport_moves_between_transport_notifications(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    playback._on_position(20.)
    QTest.qWait(60)
    positions, notifications = [], []
    timeline.viewStartSecondsChanged.connect(lambda: positions.append(float(timeline.property("viewStartSeconds"))))
    elapsed = QElapsedTimer()
    elapsed.start()
    timer = QTimer()
    timer.setInterval(50)
    def position():
        notifications.append(True)
        playback._on_position(20. + elapsed.elapsed() / 1000.)
    timer.timeout.connect(position)
    loop = QEventLoop()
    timer.start()
    QTimer.singleShot(320, loop.quit)
    loop.exec()
    timer.stop()
    assert len(positions) >= len(notifications) + 3, (len(positions), len(notifications))
    assert all(b >= a for a, b in zip(positions, positions[1:])), positions
    assert editor.state.notes == state.notes


def test_display_clock_is_bounded_and_pause_and_small_seek_are_exact(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    clock = find_visual_item(root, "visualPlaybackClock")
    assert clock is not None
    playback._on_position(20.)
    QTest.qWait(160)
    display = float(timeline.property("displayCursorSeconds"))
    assert 20 < display <= 20.051
    assert playback.positionSeconds == 20
    playback.seek(20.025)
    assert float(timeline.property("displayCursorSeconds")) == pytest.approx(20.025)
    playback._pause_requested = True  # A pause, rather than transport completion.
    playback._on_playing(False)
    QTest.qWait(80)
    assert float(timeline.property("displayCursorSeconds")) == pytest.approx(playback.positionSeconds)
    assert not clock.property("active")
    assert editor.state.notes == state.notes


def test_rate_and_backward_seek_keep_visual_and_audio_time_separate(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    playback.setPlaybackRate(2.)
    playback._on_position(40.)
    QTest.qWait(140)
    assert 40 < float(timeline.property("displayCursorSeconds")) <= 40.101
    assert playback.positionSeconds == 40
    playback.seek(12.5)
    assert float(timeline.property("displayCursorSeconds")) == pytest.approx(12.5)
    playback._pause_requested = True
    playback._on_playing(False)
    assert float(timeline.property("displayCursorSeconds")) == pytest.approx(12.5)
    QTest.qWait(50)
    # Subsequent backend notifications remain authoritative while paused.
    assert float(timeline.property("displayCursorSeconds")) == pytest.approx(playback.positionSeconds)
    assert editor.state.notes == state.notes
