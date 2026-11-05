"""Pointer-driven selection across viewport edges and current-note overview bounds."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.note import Note
from tests.qt.test_qml_smoke import load_shell, _visual_item
from tools.studio_evidence import install_memory_output


@pytest.fixture(autouse=True)
def memory_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def scene_point(canvas, x, y):
    return canvas.mapToScene(QPointF(x, y)).toPoint()


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_marquee_edge_hold_scrolls_time_and_preserves_original_anchor(qapp, qtbot, direction):
    notes = (
        Note.create(id='near', pitch=65, start_time=21, duration=.3),
        Note.create(id='far', pitch=65, start_time=16 if direction == 'left' else 28, duration=.3),
        Note.create(id='outside', pitch=40, start_time=80, duration=1),
    )
    engine, root, editor = load_shell(qapp, notes=notes, selected_ids=frozenset({'outside'}))
    root.resize(1200, 700)
    timeline = _visual_item(root.contentItem(), 'timeline')
    canvas = _visual_item(root.contentItem(), 'pianoCanvas')
    timeline.setProperty('pixelsPerSecond', 160)
    editor.setViewScale(160, 18)
    timeline.setProperty('viewportTop', 78)
    timeline.setProperty('viewStartSeconds', 20)
    qapp.processEvents()
    root.grabWindow()
    key = timeline.property('keyWidth')
    top = timeline.property('highestPitch')
    start_x = key + (2 if direction == 'left' else .5) * 160
    edge_x = key + 1 if direction == 'left' else canvas.width() - 1
    start = scene_point(canvas, start_x, (top - 68) * 18)
    finish = scene_point(canvas, edge_x, (top - 63) * 18)
    QTest.mousePress(root, Qt.LeftButton, Qt.ControlModifier, start)
    QTest.mouseMove(root, finish, delay=20)
    qtbot.waitUntil(lambda: timeline.property('viewStartSeconds') < 15 if direction == 'left'
                   else timeline.property('viewStartSeconds') + timeline.property('visibleSeconds') > 29,
                   timeout=3000)
    marquee = _visual_item(root.contentItem(), 'marqueeSelection')
    assert marquee.x() >= key
    assert marquee.x() + marquee.width() <= canvas.width()
    QTest.mouseRelease(root, Qt.LeftButton, Qt.ControlModifier, finish)
    assert editor.state.selected_ids == frozenset({'near', 'far', 'outside'})
    stopped = timeline.property('viewStartSeconds')
    qtbot.wait(100)
    assert timeline.property('viewStartSeconds') == stopped
    assert not _visual_item(root.contentItem(), 'marqueeSelection').isVisible()
    assert len(editor.state.notes) == 3
    root.close()


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_marquee_edge_hold_scrolls_pitch_and_keeps_selected_notes(qapp, qtbot, direction):
    notes = (
        Note.create(id='near', pitch=60, start_time=1.5, duration=.3),
        Note.create(id='far', pitch=82 if direction == 'up' else 38, start_time=1.5, duration=.3),
    )
    engine, root, editor = load_shell(qapp, notes=notes, selected_ids=frozenset())
    root.resize(1200, 640)
    timeline = _visual_item(root.contentItem(), 'timeline')
    canvas = _visual_item(root.contentItem(), 'pianoCanvas')
    timeline.setProperty('viewportTop', 66 if direction == 'up' else 80)
    qapp.processEvents()
    root.grabWindow()
    top = timeline.property('highestPitch')
    key = timeline.property('keyWidth')
    start_y = (top - (55 if direction == 'up' else 70)) * 18
    start = scene_point(canvas, key + 80, start_y)
    finish = scene_point(canvas, key + 200, 1 if direction == 'up' else canvas.height() - 1)
    QTest.mousePress(root, Qt.LeftButton, pos=start)
    QTest.mouseMove(root, finish, delay=20)
    qtbot.waitUntil(lambda: timeline.property('highestPitch') >= 83 if direction == 'up'
                   else timeline.property('lowestPitch') <= 37, timeout=3000)
    QTest.mouseRelease(root, Qt.LeftButton, pos=finish)
    assert editor.state.selected_ids == frozenset({'near', 'far'})
    stopped = timeline.property('highestPitch')
    qtbot.wait(100)
    assert timeline.property('highestPitch') == stopped
    root.close()


def test_escape_cancels_edge_marquee_without_creating_a_note(qapp, qtbot):
    engine, root, editor = load_shell(qapp, notes=(Note.create(id='long', pitch=60, start_time=50, duration=1),), selected_ids=frozenset())
    timeline = _visual_item(root.contentItem(), 'timeline')
    canvas = _visual_item(root.contentItem(), 'pianoCanvas')
    start = scene_point(canvas, timeline.property('keyWidth') + 30, 80)
    finish = scene_point(canvas, canvas.width() - 1, 120)
    QTest.mousePress(root, Qt.LeftButton, pos=start)
    QTest.mouseMove(root, finish, delay=20)
    qtbot.waitUntil(lambda: timeline.property('viewStartSeconds') > 0)
    QTest.keyClick(root, Qt.Key_Escape)
    QTest.mouseRelease(root, Qt.LeftButton, pos=finish)
    stopped = timeline.property('viewStartSeconds')
    qtbot.wait(100)
    assert timeline.property('viewStartSeconds') == stopped
    assert not editor.state.selected_ids and len(editor.state.notes) == 1
    assert not _visual_item(root.contentItem(), 'marqueeSelection').isVisible()
    root.close()


def test_minimap_shrinks_after_delete_despite_saved_audio_and_pedal_tail_then_undoes(qapp):
    notes = (Note.create(id='kept', pitch=60, start_time=1, duration=22),
             Note.create(id='tail', pitch=64, start_time=99, duration=1))
    engine, root, editor = load_shell(qapp, notes=notes, selected_ids=frozenset())
    state = replace(editor.state, controls=(ControlEvent.create(time=150, value=0),))
    editor._replace_project(state, saved_content=state.content, path=None)
    editor.synchronizeNow()
    timeline = _visual_item(root.contentItem(), 'timeline')
    minimap = _visual_item(root.contentItem(), 'miniMap')
    playback = engine.rootContext().contextProperty('playback')
    timeline.setProperty('viewStartSeconds', 70)
    qapp.processEvents()
    before = minimap.property('totalSeconds')
    assert before == 100 and playback.totalSeconds >= 150
    editor.selectNote('tail', False)
    editor.deleteSelected()
    qapp.processEvents()
    assert editor.estimatedDurationSeconds == 150  # Pedal data is preserved.
    assert playback.totalSeconds >= 150  # Saved audio is independently preserved.
    assert minimap.property('totalSeconds') == max(23, timeline.property('visibleSeconds'))
    assert timeline.property('viewStartSeconds') <= timeline.property('maxViewStartSeconds')
    editor.undo()
    qapp.processEvents()
    assert minimap.property('totalSeconds') == before
    assert editor.noteModel.noteDurationSeconds == 100
    root.close()
