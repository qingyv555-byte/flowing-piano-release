from PySide6.QtCore import QObject, Signal

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.midi import encode_midi
from ai_piano_studio.persistence.project_json import load_project, save_project


def _music_fields(state: ProjectState) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            note.id,
            note.pitch,
            note.start_time,
            note.duration,
            note.velocity,
            note.instrument,
        )
        for note in state.notes
    )


def test_drawn_note_keeps_music_fields_through_save_recovery_and_midi(tmp_path) -> None:
    """UI gestures may change state, but must preserve the public music format."""
    controller = EditorController(ProjectState())
    controller.setPitchRange(84, 36)
    note_id = controller.beginDrawNoteAt(80, 432)  # C4 at 1.0 seconds.
    controller.previewDrawNoteTo(160)  # Extend the drawn note to 2.0 seconds.
    controller.commitGesture()

    drawn_note = next(note for note in controller.state.notes if note.id == note_id)
    assert (drawn_note.pitch, drawn_note.start_time, drawn_note.duration, drawn_note.velocity) == (
        60, 1.0, 1.0, 75,
    )

    project_path = tmp_path / "gesture.pianoai.json"
    save_project(project_path, controller.state)
    restored = load_project(project_path)
    assert _music_fields(restored) == _music_fields(controller.state)
    assert all(note.status == "saved" for note in restored.notes)

    recovery = RecoveryStore(tmp_path / "recovery")
    recovery.write(restored, str(project_path))
    recovered, original_path = recovery.read()
    assert _music_fields(recovered) == _music_fields(controller.state)
    assert original_path == str(project_path)

    midi = encode_midi(recovered.notes, recovered.tempo)
    assert bytes((0x90, 60, 75)) in midi


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _RecordingPlayback(QObject):
    playingChanged = Signal()

    def startAudition(self, pitch: int, velocity: int) -> int:
        return 1

    def auditionNote(self, pitch: int, velocity: int) -> None:
        pass


def test_recorded_note_keeps_music_fields_through_save_recovery_and_midi(tmp_path) -> None:
    controller = EditorController(ProjectState())
    clock = _Clock()
    keyboard = KeyboardController(controller, _RecordingPlayback(), clock=clock)
    keyboard.startRecording()
    clock.advance(0.25)
    keyboard.pressKey(ord("A"))
    clock.advance(0.50)
    keyboard.releaseKey(ord("A"))
    keyboard.stopRecording()

    recorded_note = controller.state.notes[0]
    assert (recorded_note.pitch, recorded_note.start_time, recorded_note.duration, recorded_note.velocity) == (
        60, 0.25, 0.5, 80,
    )

    project_path = tmp_path / "recording.pianoai.json"
    save_project(project_path, controller.state)
    restored = load_project(project_path)
    recovery = RecoveryStore(tmp_path / "recovery")
    recovery.write(restored, str(project_path))
    recovered, _ = recovery.read()

    assert _music_fields(recovered) == _music_fields(controller.state)
    assert all(note.status == "saved" for note in recovered.notes)
    assert bytes((0x90, 60, 80)) in encode_midi(recovered.notes, recovered.tempo)
