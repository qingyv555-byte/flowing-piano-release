"""Cross-module regressions reproduced during the September product audit."""
import pytest
import wave
from PySide6.QtCore import QObject, QPointF, Qt, Q_ARG, QMetaObject, QUrl, QThread
from PySide6.QtTest import QSignalSpy, QTest
from shiboken6 import delete

from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID, MusicLayer
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.music_layer_list_model import MusicLayerListModel
from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.import_controller import AudioImportController
from ai_piano_studio.audio.importer import AudioImportSession
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.editor.note_list_model import NoteListModel
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.midi import encode_midi
from ai_piano_studio.domain.tempo import TempoMap
from tests.qt.test_qml_smoke import DeterministicPlaybackPlayer, _visual_item, load_shell


def test_audio_import_child_teardown_does_not_signal_deleted_session(qapp):
    session = AudioImportSession()
    thread = QThread(session)
    thread.destroyed.connect(lambda: session._emit_finished(session._run_generation))
    # Reproduce the stopped child's deferred destruction after its parent.
    delete(session)
    qapp.processEvents()


@pytest.mark.parametrize("route", ["Midi", "Audio"])
def test_lazy_import_consumes_each_file_once_without_qml_errors(qapp, tmp_path, route):
    target = tmp_path / ("lazy.mid" if route == "Midi" else "lazy.wav")
    if route == "Midi":
        target.write_bytes(encode_midi((Note.create(
            pitch=60, start_time=0, duration=.5),), TempoMap()))
    else:
        with wave.open(str(target), "wb") as stream:
            stream.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            stream.writeframes(b"\0\0" * 4410)
    engine, root, editor = load_shell(qapp)
    if route == "Audio":
        root.setProperty("deferNoncriticalStartup", True)
        engine.rootContext().setContextProperty("audioImport", AudioImportController(editor, parent=engine))
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    for _ in range(2):
        assert QMetaObject.invokeMethod(root, f"open{route}ImportDialog", Qt.DirectConnection,
                                       Q_ARG("QVariant", QUrl.fromLocalFile(str(target))))
        qapp.processEvents()
        dialog = root.findChild(QObject, f"{route.lower()}ImportDialog")
        assert dialog.property("opened")
        assert dialog.property("sourceUrl") == QUrl.fromLocalFile(str(target)).toString()
        dialog.close()
    assert warnings == []
    root.close()


def test_open_synced_project_updates_duration_bindings(qapp, tmp_path):
    engine, root, editor = load_shell(qapp)
    path = tmp_path / "long.pianoai.json"
    save_project(path, ProjectState((Note.create(
        pitch=60, start_time=30, duration=2),)))
    editor.loadProject(str(path))
    qapp.processEvents()
    assert editor.syncState == "synced"
    assert root.findChild(QObject, "arrangementPanel").property("durationSeconds") == 32
    assert "32.0" in root.findChild(QObject, "timelineDurationStatus").property("text")
    root.close()


def test_switch_project_updates_active_layer_binding(qapp, tmp_path):
    engine, root, editor = load_shell(qapp)
    second = editor.addMusicLayer("Bass", "bass")
    assert root.findChild(QObject, "arrangementPanel").property("activeLayerId") == second
    path = tmp_path / "default.pianoai.json"
    save_project(path, ProjectState())
    # Exercise replacement directly: public openProject first saves a dirty
    # project asynchronously, which is covered by the save/unsaved-exit suites.
    editor._load_project(str(path))
    qapp.processEvents()
    assert editor.activeLayerId == DEFAULT_LAYER_ID
    assert root.findChild(QObject, "arrangementPanel").property("activeLayerId") == DEFAULT_LAYER_ID
    root.close()


def test_active_layer_note_count_notifies_after_note_edit(qapp):
    engine, root, editor = load_shell(qapp)
    changed = QSignalSpy(editor.activeLayerDataChanged)
    before = editor.activeLayer["noteCount"]
    editor.createNoteAt(240, 144)
    assert editor.activeLayer["noteCount"] == before + 1
    assert changed.count() > 0
    root.close()


@pytest.mark.parametrize("view_mode,hidden", [("all", True), ("active", False)])
def test_marquee_does_not_select_invisible_layer_notes(qapp, view_mode, hidden):
    engine, root, editor = load_shell(qapp, selected_ids=frozenset())
    other = editor.addMusicLayer("Other", "other")
    editor.createNoteAt(100, 144)
    other_note = editor.state.selected_notes[0].id
    editor.synchronizeNow()
    editor.clearSelection()
    editor.setActiveLayer(DEFAULT_LAYER_ID)
    if hidden:
        editor.setMusicLayerProperty(other, "visible", False)
    engine.rootContext().contextProperty("experience").setLayerViewMode(view_mode)
    qapp.processEvents()
    item = _visual_item(root.contentItem(), f"noteItem-{other_note}")
    assert not item.isVisible()
    start = item.mapToScene(QPointF(-8, -8)).toPoint()
    end = item.mapToScene(QPointF(item.width() + 8, item.height() + 8)).toPoint()
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(root, end, delay=10)
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, end)
    assert other_note not in editor.state.selected_ids
    root.close()


def test_selection_and_pitch_edits_do_not_republish_music_layer_rows(qapp):
    note = Note.create(id="a", pitch=60, start_time=0, duration=1)
    state = ProjectState((note,))
    model = MusicLayerListModel(state)
    changed = QSignalSpy(model.dataChanged)
    model.set_state(state.select_only("a"))
    model.set_state(state.select_only("a").move_selected(delta_time=1, delta_pitch=1))
    assert changed.count() == 0
    # Real layer changes must still publish, including a note move across layers.
    other = MusicLayer.create(id="other", name="Other", order=1)
    state = state.add_layer(other).select_only("a").move_selected_to_layer("other")
    model.set_state(state)
    assert model.data(model.index(1), next(
        role for role, name in model.roleNames().items() if bytes(name) == b"noteCount")) == 1


def test_failed_layer_transition_restores_active_layer_and_notifications(qapp, monkeypatch):
    engine, root, editor = load_shell(qapp)
    other = editor.addMusicLayer("Other", "other")
    before = editor.state
    original = editor._schedule_sync
    attempts = []

    def fail_once():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("audit injected late refresh failure")
        original()

    monkeypatch.setattr(editor, "_schedule_sync", fail_once)
    assert not editor.deleteMusicLayer(other)
    assert editor.state == before
    assert editor.activeLayerId == other
    assert root.findChild(QObject, "arrangementPanel").property("activeLayerId") == other
    assert editor._published_layers == before.layers
    root.close()


def test_dense_smart_switch_never_constructs_segment_delegates(qapp):
    notes = tuple(Note.create(id=f"dense-{i}", pitch=60, start_time=i * .1,
                              duration=.09) for i in range(1000))
    engine, root, editor = load_shell(qapp, notes=notes, selected_ids=frozenset())
    assert editor.compositeModel.totalCount < 600
    repeater = root.findChild(QObject, "compositeNoteRepeater")
    counts = []
    repeater.countChanged.connect(lambda: counts.append(repeater.property("count")))
    experience = engine.rootContext().contextProperty("experience")
    experience.setCompositeDisplayMode("smart")
    qapp.processEvents()
    assert root.findChild(QObject, "timeline").property("useCompositeCanvas")
    assert not any(counts)
    assert repeater.property("count") == 0
    root.close()


def test_playback_clock_reuses_snapshot_but_layer_changes_invalidate_it(qapp, monkeypatch):
    calls = []
    original = ProjectState.effective_notes

    def tracked(state, *args, **kwargs):
        calls.append(True)
        return original(state, *args, **kwargs)

    monkeypatch.setattr(ProjectState, "effective_notes", tracked)
    editor = EditorController(ProjectState((Note.create(
        id="n", pitch=60, start_time=0, duration=1),)))
    playback = PlaybackController(editor, player=DeterministicPlaybackPlayer())
    playback._playback_snapshot_notes()
    calls.clear()
    for position in (.1, .2, .3):
        playback._on_position(position)
    editor.selectNote("n")
    assert not calls
    assert editor.setMusicLayerProperty(DEFAULT_LAYER_ID, "transpose", 12)
    assert playback._playback_snapshot_notes()[0].pitch == 72
    assert len(calls) == 1


def test_selection_and_viewport_do_not_invalidate_full_song_overview(qapp):
    state = ProjectState((Note.create(id="n", pitch=60, start_time=0, duration=1),))
    model = NoteListModel(state)
    overview = model.overviewByLayer
    changed = QSignalSpy(model.overviewChanged)
    model.set_state(state.select_only("n"))
    model.set_viewport(2, 4, 48, 84)
    assert model.overviewByLayer is overview
    assert changed.count() == 0
    model.set_state(state.select_only("n").move_selected(delta_time=3, delta_pitch=1))
    assert changed.count() == 1
    assert model.overviewByLayer[DEFAULT_LAYER_ID][0]["startTime"] == 3
