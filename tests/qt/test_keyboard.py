from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, Signal, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.keyboard.controller import KeyboardController
from ai_piano_studio.persistence.project_library import ProjectLibrary
from ai_piano_studio.persistence.midi import encode_midi


class FakePlayback(QObject):
    class _Player:
        def audition(self, pitch: int, velocity: int = 75) -> None:
            pass

    positionChanged = Signal()
    playingChanged = Signal()
    feedbackRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.auditions: list[tuple[int, int]] = []
        self.duration_auditions: list[tuple[int, int, float]] = []
        self.stop_auditions = 0
        self.stop_modes: list[str] = []
        self.started_voices: list[tuple[int, int, int]] = []
        self.released_voices: list[tuple[int, str]] = []
        self._next_voice_id = 1

    def startAudition(self, pitch: int, velocity: int = 75) -> int:
        voice_id = self._next_voice_id
        self._next_voice_id += 1
        self.started_voices.append((voice_id, pitch, velocity))
        return voice_id

    def releaseAudition(self, voice_id: int, mode: str = "natural") -> None:
        self.released_voices.append((voice_id, mode))

    def auditionNote(self, pitch: int, velocity: int = 75) -> None:
        self.auditions.append((pitch, velocity))

    def auditionNoteWithDuration(self, pitch: int, velocity: int, duration: float) -> None:
        self.duration_auditions.append((pitch, velocity, duration))

    def stopAudition(self, mode: str = "safe") -> None:
        self.stop_auditions += 1
        self.stop_modes.append(mode)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RoutingPlayer(QObject):
    """Minimal audio-player boundary used to observe real controller routing."""

    positionChanged = Signal(float)
    playingChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._next_voice_id = 1
        self.started_voices: list[tuple[int, int, int, float | None]] = []
        self.released_voices: list[tuple[int, str]] = []
        self.stop_modes: list[str] = []
        self.stop_durations: list[float] = []
        self.duration_at_stop = None

    def audition(
        self, pitch: int, velocity: int, duration: float | None = None
    ) -> int:
        voice_id = self._next_voice_id
        self._next_voice_id += 1
        self.started_voices.append((voice_id, pitch, velocity, duration))
        return voice_id

    def release_audition(self, voice_id: int, mode) -> None:
        self.released_voices.append((voice_id, str(mode)))
        if str(mode) == 'safe' and self.duration_at_stop is not None:
            self.stop_durations.append(self.duration_at_stop())

    def stop_audition(self, mode) -> None:
        self.stop_modes.append(str(mode))
        if self.duration_at_stop is not None:
            self.stop_durations.append(self.duration_at_stop())


class DurationTrackingEditor(EditorController):
    def __init__(self, state: ProjectState, **kwargs) -> None:
        super().__init__(state, **kwargs)
        self.duration_previews: list[tuple[str, float]] = []

    def previewRecordedEvents(self, notes, controls):
        self.duration_previews.extend((n.id,n.duration) for n in notes)
        super().previewRecordedEvents(notes, controls)

    def previewNoteDuration(self, note_id: str, duration: float) -> None:
        self.duration_previews.append((note_id, duration))
        super().previewNoteDuration(note_id, duration)


def make_keyboard(
    notes: tuple[Note, ...] = (),
) -> tuple[EditorController, FakePlayback, FakeClock, KeyboardController]:
    # Keep autosave traffic inside pytest's disposable workspace.  A running
    # desktop app must never make keyboard tests touch the user's project list.
    editor = EditorController(
        ProjectState(notes),
        project_library=ProjectLibrary(Path(".pytest-tmp") / "keyboard-projects"),
    )
    playback = FakePlayback()
    clock = FakeClock()
    keyboard = KeyboardController(editor, playback, clock=clock)
    return editor, playback, clock, keyboard


def make_real_keyboard(
    notes: tuple[Note, ...] = (),
) -> tuple[EditorController, RoutingPlayer, PlaybackController, FakeClock, KeyboardController]:
    editor = DurationTrackingEditor(
        ProjectState(notes),
        project_library=ProjectLibrary(Path(".pytest-tmp") / "real-keyboard-projects"),
    )
    player = RoutingPlayer()
    playback = PlaybackController(editor, player)
    clock = FakeClock()
    keyboard = KeyboardController(editor, playback, clock=clock)
    return editor, player, playback, clock, keyboard


def test_press_key_auditions_note_without_recording() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    assert keyboard.pressKey(ord("A")) is True
    assert playback.started_voices == [(1, 60, 75)]
    assert editor.noteModel.rowCount() == 0
    assert keyboard.recording is False


def test_press_unknown_key_does_nothing() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    assert keyboard.pressKey(ord("Q")) is False
    assert playback.started_voices == []


def test_octave_shift_changes_pitch_and_clamps() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.octaveUp()
    assert keyboard.octave == 5
    keyboard.pressKey(ord("A"))
    assert playback.started_voices[-1] == (1, 72, 75)
    for _ in range(10):
        keyboard.octaveUp()
    assert keyboard.octave == 7
    for _ in range(20):
        keyboard.octaveDown()
    assert keyboard.octave == 1


def test_recording_creates_notes_with_press_and_release_timing() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    clock.advance(0.30)
    keyboard.pressKey(ord("A"))
    clock.advance(0.40)
    keyboard.releaseKey(ord("A"))
    clock.advance(0.05)
    keyboard.pressKey(ord("J"))
    keyboard.stopRecording()

    notes = editor.state.notes
    assert [(note.pitch, note.start_time) for note in notes] == [
        (60, pytest.approx(0.30)),
        (71, pytest.approx(0.75)),
    ]
    assert notes[0].duration == pytest.approx(0.40)
    assert notes[1].duration == pytest.approx(0.03125)
    assert keyboard.recording is False


def test_recording_preserves_real_key_down_and_key_up_timing() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    clock.advance(0.30)
    keyboard.pressKey(ord("A"))
    clock.advance(0.40)
    keyboard.releaseKey(ord("A"))
    keyboard.stopRecording()

    note = editor.state.notes[0]
    assert note.start_time == pytest.approx(0.30)
    assert note.duration == pytest.approx(0.40)


def test_recording_release_keeps_exact_duration_and_releases_its_voice() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.pressPitch(60)
    voice_id = playback.started_voices[-1][0]

    clock.advance(0.40)
    keyboard.releasePitch(60)

    keyboard._tick()  # UI projection is batched; the event timestamp is already captured.
    assert editor.state.notes[0].duration == pytest.approx(0.40)
    assert playback.released_voices == [(voice_id, "natural")]
    released_state = editor.state
    released_midi = encode_midi(editor.state.notes, editor.state.tempo)

    clock.advance(2.0)

    assert editor.state is released_state
    assert editor.state.notes[0].duration == pytest.approx(0.40)
    assert encode_midi(editor.state.notes, editor.state.tempo) == released_midi


def test_real_playback_routes_simultaneous_key_releases_by_voice_id() -> None:
    editor, player, playback, clock, keyboard = make_real_keyboard()

    keyboard.pressPitch(60)
    keyboard.pressPitch(64)
    c4_voice_id, e4_voice_id = [voice_id for voice_id, *_rest in player.started_voices]

    keyboard.releasePitch(60)

    assert player.released_voices == [(c4_voice_id, "natural")]
    assert e4_voice_id not in [voice_id for voice_id, _mode in player.released_voices]

    keyboard.releasePitch(64)

    assert player.released_voices == [
        (c4_voice_id, "natural"),
        (e4_voice_id, "natural"),
    ]


def test_recording_release_all_finalizes_duration_once_before_safe_audio_stop() -> None:
    editor, player, playback, clock, keyboard = make_real_keyboard()

    keyboard.startRecording()
    keyboard.pressPitch(60)
    note_id = next(iter(keyboard._take_notes))
    player.duration_at_stop = lambda: editor.state.notes[0].duration
    clock.advance(0.40)

    keyboard.releaseAll()

    assert editor.duration_previews == [(note_id, pytest.approx(0.40))]
    assert editor.state.notes[0].duration == pytest.approx(0.40)
    assert player.stop_durations == [pytest.approx(0.40)]
    # Source-owned cleanup replaces the old global stop contract. The duration
    # must still be finalized before releasing the corresponding live voice.
    assert player.released_voices == [(1, 'safe')]
    assert player.stop_modes == []


def test_held_recording_note_grows_on_recording_tick() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    clock.advance(0.10)
    keyboard.pressKey(ord("A"))
    clock.advance(0.18)
    keyboard._tick()

    note = editor.state.notes[0]
    assert note.start_time == pytest.approx(0.10)
    assert note.duration == pytest.approx(0.18)


def test_recording_overdubs_from_transport_position() -> None:
    existing = Note.create(id="old", pitch=60, start_time=2.0, duration=1.0)
    editor, playback, clock, keyboard = make_keyboard((existing,))
    keyboard.startRecording()
    keyboard.pressKey(ord("S"))
    keyboard.stopRecording()

    new_note = editor.state.notes[-1]
    assert new_note.start_time == 0.0
    assert editor.state.notes[0].id == "old"


def test_recording_is_a_single_undo_step() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    for key in (ord("A"), ord("S"), ord("D")):
        keyboard.pressKey(key)
        keyboard.releaseKey(key)
        clock.advance(0.3)
    keyboard.stopRecording()

    assert editor.noteModel.rowCount() == 3
    editor.undo()
    assert editor.noteModel.rowCount() == 0
    editor.redo()
    assert editor.noteModel.rowCount() == 3


def test_stop_recording_selects_recorded_notes() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.pressKey(ord("A"))
    keyboard.pressKey(ord("S"))
    keyboard.stopRecording()

    assert editor.selectedCount == 2


def test_stop_recording_without_notes_emits_no_feedback() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.stopRecording()
    assert editor.noteModel.rowCount() == 0
    assert editor.canUndo is False


def test_held_notes_are_finished_when_recording_stops() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.pressKey(ord("A"))
    clock.advance(1.1)
    keyboard.stopRecording()

    note = editor.state.notes[0]
    assert note.duration == pytest.approx(1.1)


def test_record_seconds_tracks_clock(qtbot) -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    clock.advance(1.0)
    keyboard._tick()
    assert keyboard.recordSeconds == 1.0
    keyboard.stopRecording()


def test_record_cursor_advances_from_transport_position() -> None:
    existing = Note.create(id="old", pitch=60, start_time=2.0, duration=1.0)
    editor, playback, clock, keyboard = make_keyboard((existing,))
    keyboard.startRecording()
    clock.advance(0.75)
    keyboard._tick()

    assert keyboard.recordCursorSeconds == pytest.approx(0.75)


def test_press_pitch_auditions_without_recording() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.pressPitch(64)
    assert playback.started_voices == [(1, 64, 75)]
    assert editor.noteModel.rowCount() == 0


def test_pressing_a_black_piano_key_auditions_its_semitone() -> None:
    editor, playback, clock, keyboard = make_keyboard()

    keyboard.pressPitch(61)  # C#4

    assert playback.started_voices == [(1, 61, 75)]
    assert editor.noteModel.rowCount() == 0


def test_virtual_piano_uses_configured_preview_defaults_outside_recording() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.setPreviewDefaults(96, 1.0)

    keyboard.pressPitch(61)

    assert playback.started_voices == [(1, 61, 96)]
    assert editor.noteModel.rowCount() == 0


def test_tap_pitch_recording_starts_an_audible_recorded_note(qtbot) -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.setPreviewDefaults(75, 0.08)
    keyboard.startRecording()

    keyboard.tapPitch(61)

    assert playback.started_voices == [(1, 61, 80)]
    qtbot.waitUntil(lambda: editor.noteModel.rowCount() == 1)
    assert editor.noteModel.rowCount() == 1
    assert editor.state.notes[0].pitch == 61
    qtbot.waitUntil(lambda: playback.released_voices == [(1, "natural")], timeout=300)


def test_tapping_virtual_piano_plays_the_default_duration_without_release() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.setPreviewDefaults(96, 1.0)
    note_ons: list[int] = []
    keyboard.keyNoteOn.connect(note_ons.append)

    keyboard.tapPitch(61)

    assert playback.started_voices == [(1, 61, 96)]
    assert playback.stop_auditions == 0
    assert note_ons == [61]
    assert editor.noteModel.rowCount() == 0


def test_releasing_all_keys_invalidates_prior_tap_release_callbacks(qapp, qtbot) -> None:
    """A route change must not let an old tap end a new tap of the same key."""
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.setPreviewDefaults(75, 0.08)
    note_offs: list[int] = []
    keyboard.keyNoteOff.connect(note_offs.append)

    keyboard.tapPitch(61)
    qtbot.waitUntil(lambda: len(playback.started_voices) == 1, timeout=200)
    keyboard.releaseAll()
    keyboard.tapPitch(61)
    qtbot.waitUntil(lambda: len(playback.started_voices) == 2, timeout=200)
    qtbot.waitUntil(lambda: note_offs == [61], timeout=200)

    assert note_offs == [61]
    assert playback.released_voices == [(1, 'safe')]

    qtbot.waitUntil(lambda: note_offs == [61, 61], timeout=200)
    assert playback.released_voices == [(1, 'safe'), (2, "natural")]


def test_releasing_virtual_piano_pitch_stops_its_audition() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.pressPitch(60)
    keyboard.releasePitch(60)

    assert playback.released_voices == [(1, "natural")]


def test_press_pitch_rejects_out_of_range() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.pressPitch(-1)
    keyboard.pressPitch(128)
    assert playback.started_voices == []


def test_key_note_signals_track_press_and_release() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    note_ons: list[int] = []
    note_offs: list[int] = []
    keyboard.keyNoteOn.connect(note_ons.append)
    keyboard.keyNoteOff.connect(note_offs.append)
    keyboard.pressPitch(64)
    keyboard.releasePitch(64)
    keyboard.pressKey(ord("A"))
    keyboard.releaseKey(ord("A"))
    assert note_ons == [64, 60]
    assert note_offs == [64, 60]


def test_press_pitch_records_like_keyboard() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    clock.advance(0.30)
    keyboard.pressPitch(67)
    clock.advance(0.40)
    keyboard.releasePitch(67)
    keyboard.stopRecording()

    notes = editor.state.notes
    assert notes[0].pitch == 67
    assert notes[0].start_time == pytest.approx(0.30)
    assert notes[0].duration == pytest.approx(0.40)


def test_recording_exposes_the_latest_recorded_note_id() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    keyboard.startRecording()
    keyboard.pressKey(ord("A"))
    keyboard._tick()

    assert keyboard.lastRecordedNoteId == editor.state.notes[0].id


def test_key_note_off_emitted_when_recording_stops() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    note_offs: list[int] = []
    keyboard.keyNoteOff.connect(note_offs.append)
    keyboard.startRecording()
    keyboard.pressPitch(62)
    keyboard.stopRecording()
    assert note_offs == [62]


def test_application_deactivation_releases_held_keys_and_stops_audition() -> None:
    editor, playback, clock, keyboard = make_keyboard()
    note_offs: list[int] = []
    keyboard.keyNoteOff.connect(note_offs.append)
    keyboard.setInputEnabled(True)
    assert keyboard.eventFilter(
        keyboard,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier),
    ) is True

    assert keyboard.eventFilter(keyboard, QEvent(QEvent.Type.ApplicationDeactivate)) is False

    assert keyboard._held == {}
    assert note_offs == [60]
    assert playback.released_voices == [(1, 'safe')]
    assert playback.stop_modes == []
