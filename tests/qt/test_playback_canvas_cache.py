"""Long-score follow playback must translate retained notes, not repaint them."""
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session, find_visual_item


@pytest.fixture(autouse=True)
def memory_audio_only(monkeypatch):
    from tools.studio_evidence import install_memory_output
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def prepare(session):
    app, engine, root, editor, playback = session
    root.resize(1280, 800)
    notes = tuple(Note.create(id=f"long-{i}", pitch=60 + i % 12,
                              start_time=i * .04, duration=.15)
                  for i in range(1800)) + (
        Note.create(id="held", pitch=74, start_time=10, duration=40),
        Note.create(id="editable", pitch=76, start_time=17, duration=.4),
    )
    state = ProjectState(notes)
    editor._replace_project(state, saved_content=state.content, path=None)
    root.setProperty("pageState", "editor")
    app.processEvents()
    timeline = find_visual_item(root, "timeline")
    timeline.setProperty("viewportTop", 84)
    playback._on_playing(True)
    playback._on_position(15)
    QTest.qWait(80)
    root.grabWindow()
    return app, root, editor, playback, timeline, state


def test_following_a_long_score_does_not_repaint_unchanged_note_bodies(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    canvas = find_visual_item(root, "rawNoteCanvas")
    viewport = editor.noteModel._viewport
    paints = []
    canvas.painted.connect(lambda: paints.append(True))
    for step in range(1, 9):
        playback._on_position(15 + step * .02)
        QTest.qWait(20)
        root.grabWindow()
    # The real failure was one full piano-roll texture paint per clock tick.
    # A fixed overscan slice needs no such redraw while it moves on screen.
    assert len(paints) <= 2, f"unchanged score repainted {len(paints)} times"
    assert editor.noteModel._viewport == viewport
    assert editor.state.notes == state.notes
    assert float(timeline.property("viewStartSeconds")) > 0
    held = find_visual_item(root, "cachedPlaybackHighlight-held")
    assert held is not None and held.isVisible()
    playback._on_position(50.01)
    app.processEvents()
    assert find_visual_item(root, "cachedPlaybackHighlight-held") is None


def test_cached_scroll_keeps_note_hit_testing_in_viewport_coordinates(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    area = find_visual_item(root, "pianoCanvas")
    raw = find_visual_item(root, "rawNoteCanvas")
    assert float(raw.x()) < 0  # Exercise the translated cache, not the idle path.
    x = float(timeline.property("keyWidth")) + (
        17 - float(timeline.property("viewStartSeconds"))) * float(timeline.property("pixelsPerSecond")) + 8
    y = (float(timeline.property("highestPitch")) - 76) * float(timeline.property("rowHeight")) + 5
    point = area.mapToScene(QPointF(x, y)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, pos=point)
    app.processEvents()
    assert editor.state.selected_ids == frozenset({"editable"})
    assert editor.state.notes == state.notes
    assert raw.property("cachedPlayback")


def test_pause_returns_to_live_editing_and_releases_active_highlights(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    canvas = find_visual_item(root, "rawNoteCanvas")
    playback._on_playing(False)
    app.processEvents()
    assert not canvas.property("cachedPlayback")
    assert float(canvas.x()) == 0
    assert float(canvas.width()) == pytest.approx(float(timeline.width()))
    assert find_visual_item(root, "cachedPlaybackHighlight-held") is None
    editor.selectNote("editable", False)
    editor.beginGesture()
    editor.previewMovePixels(20, 0)
    editor.commitGesture()
    assert editor.state.notes != state.notes
    editor.undo()
    assert editor.state.notes == state.notes


def test_cache_rebases_and_only_runs_for_the_dense_original_view(studio_session):
    app, root, editor, playback, timeline, state = prepare(studio_session)
    canvas = find_visual_item(root, "rawNoteCanvas")
    previous_start = float(canvas.property("paintViewStart"))
    playback._on_position(45)
    QTest.qWait(80)
    root.grabWindow()
    assert float(canvas.property("paintViewStart")) > previous_start
    assert canvas.property("cachedPlayback")
    held = find_visual_item(root, "cachedPlaybackHighlight-held")
    assert held is not None and held.isVisible()
    timeline.setProperty("displayMode", "smart")
    app.processEvents()
    assert not canvas.property("cachedPlayback")
    assert find_visual_item(root, "cachedPlaybackHighlight-held") is None
    timeline.setProperty("displayMode", "original")
    app.processEvents()
    assert canvas.property("cachedPlayback")
    small = ProjectState((state.notes[-2],))
    editor._replace_project(small, saved_content=small.content, path=None)
    app.processEvents()
    assert not canvas.property("cachedPlayback")
    assert find_visual_item(root, "cachedPlaybackHighlight-held") is None
