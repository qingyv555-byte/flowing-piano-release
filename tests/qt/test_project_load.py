import json

from PySide6.QtCore import QObject, Signal

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.persistence.project_json import project_to_dict, save_project


def seeded_controller() -> EditorController:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    return EditorController(ProjectState((note,), frozenset({"a"})))


def test_load_project_saves_dirty_project_before_switching(qtbot, tmp_path) -> None:
    current = tmp_path / "current.pianoai.json"
    target = tmp_path / "song.pianoai.json"
    notes = (
        Note.create(id="x", pitch=62, start_time=1.0, duration=0.5),
        Note.create(id="y", pitch=65, start_time=2.0, duration=0.5),
    )
    save_project(target, ProjectState(notes))

    controller = seeded_controller()
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(current))
    controller.setBpm(90)
    assert controller.isDirty is True
    with qtbot.waitSignal(controller.loadSucceeded, timeout=500):
        controller.loadProject(str(target))

    assert [note.id for note in controller.state.notes] == ["x", "y"]
    assert controller.isDirty is False
    assert controller.canUndo is False
    assert json.loads(current.read_text(encoding="utf-8"))["bpm"] == 90


def test_opening_current_path_again_succeeds_without_reloading(qtbot, tmp_path) -> None:
    target = tmp_path / "current.pianoai.json"
    saved = ProjectState(
        (Note.create(id="saved", pitch=60, start_time=0.0, duration=0.5),)
    )
    save_project(target, saved)
    controller = EditorController()
    with qtbot.waitSignal(controller.loadSucceeded, timeout=500):
        controller.loadProject(str(target))
    controller.setBpm(90)
    state_before = controller.state
    session_before = controller.projectSessionId
    undo_depth_before = controller._history.undo_depth
    successes: list[str] = []
    controller.loadSucceeded.connect(successes.append)
    target.unlink()

    controller.openProject(str(target))

    assert successes == [str(target.resolve())]
    assert controller.state == state_before
    assert controller.projectSessionId == session_before
    assert controller._history.undo_depth == undo_depth_before
    assert controller.isDirty is True


class PlaybackProbe(QObject):
    positionChanged = Signal(float)
    playingChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.played = []

    def set_volume(self, _volume: float) -> None:
        pass

    def set_playback_rate(self, _rate: float) -> None:
        pass

    def play_notes(self, notes) -> None:
        self.played.append(tuple(notes))


def test_loaded_project_playback_uses_the_saved_snapshot(tmp_path) -> None:
    target = tmp_path / "song.pianoai.json"
    editable = Note.create(
        id="editable",
        pitch=72,
        start_time=4.0,
        duration=0.25,
        status="pending",
    )
    saved = Note.create(
        id="editable",
        pitch=60,
        start_time=0.0,
        duration=1.0,
    )
    target.write_text(
        json.dumps(
            project_to_dict(ProjectState((editable,), playback_notes=(saved,)))
        ),
        encoding="utf-8",
    )
    controller = EditorController(ProjectState())
    controller.loadProject(str(target))
    player = PlaybackProbe()

    PlaybackController(controller, player).play()

    assert player.played == [(saved,)]


def test_load_project_rejects_missing_file(qtbot, tmp_path) -> None:
    controller = seeded_controller()
    with qtbot.waitSignal(controller.loadFailed, timeout=500):
        controller.loadProject(str(tmp_path / "missing.json"))
    assert controller.noteModel.rowCount() == 1


def test_export_midi_writes_file(qtbot, tmp_path) -> None:
    controller = seeded_controller()
    target = tmp_path / "song.mid"
    with qtbot.waitSignal(controller.midiExported, timeout=500):
        controller.exportMidi(str(target))
    assert target.read_bytes()[:4] == b"MThd"


def test_export_midi_without_notes_fails(qtbot, tmp_path) -> None:
    controller = EditorController(ProjectState())
    with qtbot.waitSignal(controller.midiFailed, timeout=500):
        controller.exportMidi(str(tmp_path / "empty.mid"))


def test_select_notes_slot_selects_given_ids() -> None:
    first = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    second = Note.create(id="b", pitch=64, start_time=1.0, duration=0.5)
    controller = EditorController(ProjectState((first, second)))
    controller.selectNotes(["a", "b"])
    assert controller.selectedCount == 2
    assert controller.canUndo is False  # selection does not pollute history


def test_preview_add_note_requires_gesture() -> None:
    controller = seeded_controller()
    assert controller.previewAddNote(60, 0.0, 0.5, 75) == ""
    controller.beginGesture()
    note_id = controller.previewAddNote(64, 1.0, 0.5, 80)
    assert note_id
    controller.previewNoteDuration(note_id, 1.25)
    controller.commitGesture()
    added = controller.state.notes[-1]
    assert (added.pitch, added.start_time, added.duration) == (64, 1.0, 1.25)
