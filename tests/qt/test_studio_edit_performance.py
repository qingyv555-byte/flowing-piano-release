"""Guards for the measured edit publication costs, without timing-flaky gates."""
import cProfile

from PySide6.QtCore import QPersistentModelIndex, QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.editor.note_list_model import NoteListModel
from ai_piano_studio.editor.composite import CompositeNoteGrouper, CompositeGroupingConfig
from tests.qt.test_studio_shell import studio_session, preserved_qsettings_state
from tests.qt.test_qml_smoke import _visual_item


def test_appending_and_undo_keep_existing_note_indexes_alive(qapp):
    initial = ProjectState((Note.create(id="kept", pitch=60, start_time=0, duration=.5),))
    model = NoteListModel(initial)
    assert len(model.overviewByLayer["layer-default"]) == 1
    observed_overviews = []
    model.rowsInserted.connect(lambda *_: observed_overviews.append(len(model.overviewByLayer["layer-default"])))
    index = QPersistentModelIndex(model.index(0, 0))
    inserted = QSignalSpy(model.rowsInserted)
    removed = QSignalSpy(model.rowsRemoved)
    model.set_state(initial.add_note(Note.create(id="added", pitch=64, start_time=1, duration=.5)))
    assert index.isValid(), "creating one note reset every existing delegate"
    assert model.rowCount() == 2
    assert inserted.count() == 1
    assert observed_overviews == [2], "row observers saw the stale pre-insertion overview"
    model.set_state(initial)
    assert index.isValid(), "undoing append reset every existing delegate"
    assert removed.count() == 1
    assert model.rowCount() == 1


def test_selection_publishes_only_changed_rows_and_selection_role(qapp):
    initial = ProjectState(tuple(Note.create(id=f"n-{i}", pitch=60, start_time=i, duration=.5)
                                 for i in range(1000)), frozenset({"n-4"}))
    model = NoteListModel(initial)
    events = []
    model.dataChanged.connect(lambda first, last, roles: events.append((first.row(), last.row(), roles)))
    model.set_state(initial.select_only("n-6"))
    selected_role = next(role for role, name in model.roleNames().items() if bytes(name) == b"selected")
    assert events == [(4, 4, [selected_role]), (6, 6, [selected_role])]
    assert model.data(model.index(4, 0), selected_role) is False
    assert model.data(model.index(6, 0), selected_role) is True


def test_active_layer_projection_does_not_rescan_score_for_each_qml_binding(qapp):
    editor = EditorController(ProjectState(tuple(Note.create(id=f"n-{i}", pitch=60,
                              start_time=i, duration=.5) for i in range(1000))))
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(30):
        assert editor.activeLayer["noteCount"] == 1000
    profiler.disable()
    scans = sum(item.callcount for item in profiler.getstats()
                if getattr(item.code, "co_name", None) == "active_notes")
    assert scans <= 1, f"30 bindings rescanned the unchanged score {scans} times"
    editor.createNoteAt(120, 120)
    assert editor.activeLayer["noteCount"] == 1001
    assert editor._history.undo_depth == 1
    editor.undo()
    assert editor.activeLayer["noteCount"] == 1000


def test_one_new_note_does_not_rebuild_unchanged_group_summaries():
    notes = tuple(Note.create(id=f"n-{i}", pitch=60, start_time=i, duration=.2)
                  for i in range(1000))
    grouper = CompositeNoteGrouper()
    original = grouper.group(notes)
    profiler = cProfile.Profile()
    profiler.enable()
    after = grouper.group((*notes, Note.create(id="new", pitch=84, start_time=.5, duration=.2)))
    profiler.disable()
    summaries = sum(item.callcount for item in profiler.getstats()
                    if getattr(item.code, "co_name", None) == "_as_group")
    assert summaries == 1, f"one addition rebuilt {summaries} range summaries"
    assert len(after) == 1001
    assert after[0] == original[0]
    assert after[1].child_note_ids == ("new",)
    assert after[2:] == original[1:]


def test_group_summary_reuse_observes_note_changes_and_classification_config():
    first = Note.create(id="a", pitch=60, start_time=0, duration=.2)
    second = Note.create(id="b", pitch=60, start_time=.2, duration=.2)
    grouper = CompositeNoteGrouper()
    assert grouper.group((first, second))[0].end_time == .4
    changed = second.with_changes(duration=.3)
    assert grouper.group((first, changed))[0].end_time == .5
    singles = grouper.group((first, changed), config=CompositeGroupingConfig(min_child_count=3))
    assert len(singles) == 2
    assert all(not group.is_composite for group in singles)
    one = grouper.group((first,), config=CompositeGroupingConfig(min_child_count=1))
    assert one[0].is_composite


def test_hidden_minimap_does_not_project_full_score_and_repaints_when_shown(studio_session):
    app, engine, root, editor, playback = studio_session
    # Hidden remains cheap when explicitly requested; new Studio profiles show it.
    experience = engine.rootContext().contextProperty("experience")
    experience.setMiniMapDisplayMode("hidden")
    initial = ProjectState(tuple(Note.create(id=f"n-{i}", pitch=48, start_time=i*.04, duration=.2)
                                 for i in range(1000)))
    editor._replace_project(initial, saved_content=initial.content, path=None)
    root.setProperty("pageState", "editor")
    app.processEvents()
    root.grabWindow()
    minimap = _visual_item(root.contentItem(), "miniMap")
    assert not minimap.isVisible()
    profiler = cProfile.Profile()
    profiler.enable()
    editor.createNoteAt(180, 20)
    app.processEvents()
    root.grabWindow()
    profiler.disable()
    full_projections = sum(item.callcount for item in profiler.getstats()
                           if getattr(item.code, "co_name", None) in {"overviewByLayer", "minimapJson"})
    assert full_projections == 0, f"hidden minimap fetched the full score {full_projections} times"
    experience.setMiniMapDisplayMode("visible")
    app.processEvents()
    image = root.grabWindow()
    assert not image.isNull()
    assert minimap.isVisible()
    assert _visual_item(root.contentItem(), "miniMapDenseNoteCanvas").isVisible()
    assert len(editor.noteModel.overviewByLayer["layer-default"]) == 1001


def test_studio_blank_click_publishes_one_complete_configured_note(studio_session):
    app, engine, root, editor, playback = studio_session
    root.setProperty("pageState", "editor")
    experience = engine.rootContext().contextProperty("experience")
    experience.setNewNoteLengthBeats(2)
    experience.setNewNoteVelocity(96)
    editor.setBpm(120)
    app.processEvents()
    canvas = _visual_item(root.contentItem(), "drawNoteArea")
    point = canvas.mapToScene(QPointF(260, 90)).toPoint()
    before = editor.state.content
    depth = editor._history.undo_depth
    published = QSignalSpy(editor.stateChanged)
    auditions = QSignalSpy(editor.noteCreated)
    QTest.mouseClick(root, Qt.LeftButton, pos=point)
    assert editor.noteModel.totalCount == 1
    note = editor.state.notes[0]
    assert note.duration == 1.0 and note.velocity == 96
    assert editor.state.selected_ids == frozenset({note.id})
    assert editor._history.undo_depth == depth + 1
    assert auditions.count() == 1
    assert auditions.at(0) == [note.pitch, 96, 1.0]
    assert published.count() == 1, "a click published both an unselected preview and the final state"
    editor.undo()
    assert editor.state.content == before
