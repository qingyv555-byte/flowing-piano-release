from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import pytest
from PySide6.QtCore import QObject, Signal

from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController


class FakeTemporaryPlayer(QObject):
    positionChanged = Signal(float)
    playingChanged = Signal(bool)
    playbackFailed = Signal(str)
    temporaryPlayingChanged = Signal(bool)
    temporaryPositionChanged = Signal(float)
    temporaryFinished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.played: list[tuple[Note, ...]] = []
        self.temporary_notes: tuple[Note, ...] | None = None
        self.temporary_play_calls = 0
        self.stop_temporary_calls = 0
        self.finish_temporary_calls = 0
        self.seek_positions: list[float] = []
        self.stop_calls = 0
        self.stop_audition_modes: list[ReleaseMode] = []
        self.volume = None
        self.playback_rate = None

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def set_playback_rate(self, rate: float) -> None:
        self.playback_rate = rate

    def play_notes(self, notes: tuple[Note, ...]) -> None:
        self.played.append(notes)
        self.playingChanged.emit(True)

    def seek(self, seconds: float) -> None:
        self.seek_positions.append(float(seconds))
        self.positionChanged.emit(float(seconds))

    def pause(self) -> None:
        self.playingChanged.emit(False)

    def stop(self, _mode: ReleaseMode = ReleaseMode.STOP, *, after_stopped=None) -> None:
        self.stop_calls += 1
        self.playingChanged.emit(False)
        self.positionChanged.emit(0.0)
        if after_stopped is not None:
            after_stopped()

    def stop_audition(self, mode: ReleaseMode) -> None:
        self.stop_audition_modes.append(mode)

    def play_temporary_notes(self, notes: tuple[Note, ...]) -> None:
        self.temporary_play_calls += 1
        self.temporary_notes = notes
        self.temporaryPlayingChanged.emit(True)

    def stop_temporary_notes(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        self.stop_temporary_calls += 1
        self.temporary_notes = None
        self.temporaryPlayingChanged.emit(False)

    def finish_temporary_notes(self) -> None:
        self.finish_temporary_calls += 1
        self.temporary_notes = None

    def finish_temporary(self) -> None:
        self.temporaryPlayingChanged.emit(False)
        self.temporaryFinished.emit()


class ReentrantFinishPlayer(FakeTemporaryPlayer):
    """Model a stale completion edge arriving while the old session stops."""

    def __init__(self) -> None:
        super().__init__()
        self.emit_finish_while_stopping = False

    def stop_temporary_notes(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        super().stop_temporary_notes(mode)
        if self.emit_finish_while_stopping:
            self.emit_finish_while_stopping = False
            self.temporaryFinished.emit()

    def finish_temporary_notes(self) -> None:
        super().finish_temporary_notes()
        if self.emit_finish_while_stopping:
            self.emit_finish_while_stopping = False
            self.temporaryFinished.emit()


def make_note(note_id: str, start_time: float, pitch: int, duration: float) -> Note:
    return Note.create(
        id=note_id,
        pitch=pitch,
        start_time=start_time,
        duration=duration,
        velocity=80,
    )


@dataclass
class PlaybackFixture:
    editor: EditorController
    playback: PlaybackController
    player: FakeTemporaryPlayer


def make_playback(
    notes: tuple[Note, ...], selected: set[str]
) -> PlaybackFixture:
    editor = EditorController(ProjectState(notes, frozenset(selected)))
    player = FakeTemporaryPlayer()
    return PlaybackFixture(editor, PlaybackController(editor, player), player)


def test_play_selection_rebases_start_but_preserves_gaps_and_ids(qapp) -> None:
    notes = (
        make_note("a", 1.0, 60, 0.2),
        make_note("b", 1.5, 64, 0.2),
        make_note("c", 3.0, 67, 0.2),
    )
    fixture = make_playback(notes, selected={"a", "b", "c"})

    fixture.playback.selectionAudition.playSelection()

    assert fixture.player.temporary_notes is not None
    assert [note.id for note in fixture.player.temporary_notes] == ["a", "b", "c"]
    assert [note.start_time for note in fixture.player.temporary_notes] == pytest.approx(
        [0.0, 0.5, 2.0]
    )
    assert fixture.editor.state.notes == notes
    assert fixture.editor.isDirty is False


def test_play_selection_with_no_selection_is_silent_and_preserves_main_position(
    qapp,
) -> None:
    notes = (make_note("a", 1.0, 60, 50.0),)
    fixture = make_playback(notes, selected=set())
    fixture.playback.seek(45.2)

    fixture.playback.selectionAudition.playSelection()

    assert fixture.player.temporary_notes is None
    assert fixture.player.temporary_play_calls == 0
    assert fixture.playback.positionSeconds == pytest.approx(45.2)


def test_empty_selection_request_stops_previous_temporary_audio(qapp):
    fixture = make_playback((make_note('a', 1, 60, 5),), {'a'})
    fixture.playback.selectionAudition.playSelection()
    fixture.editor.clearSelection()
    fixture.playback.selectionAudition.playSelection()
    assert fixture.player.temporary_notes is None
    assert not fixture.playback.selectionAudition.playing


def test_context_clips_notes_crossing_window_boundaries(qapp):
    fixture = make_playback((make_note('bass', 0, 48, 30), make_note('a', 5, 60, 1)), {'a'})
    fixture.playback.selectionAudition.playContext()
    bass = next(n for n in fixture.player.temporary_notes if n.id == 'bass')
    assert bass.start_time == 0
    assert bass.duration == pytest.approx(2.0)


def test_main_seek_cancels_audition_before_moving_main_playhead(qapp):
    fixture = make_playback((make_note('a', 1, 60, 5),), {'a'})
    fixture.playback.selectionAudition.playSelection()
    fixture.playback.seek(3.0)
    assert not fixture.playback.selectionAudition.playing
    assert fixture.player.temporary_notes is None
    assert fixture.playback.positionSeconds == 3.0


def test_new_project_notifies_selection_solo_reset(qapp):
    fixture = make_playback((make_note('a', 1, 60, 5),), {'a'})
    audition = fixture.playback.selectionAudition
    audition.setSoloSelection(True)
    seen = []
    audition.soloSelectionChanged.connect(lambda: seen.append(audition.soloSelection))
    fixture.editor.newProject()
    assert seen == [False]


def test_context_includes_half_second_neighbors_and_loop_restarts_without_leak(
    qapp,
) -> None:
    notes = tuple(make_note(str(i), i * 0.5, 60 + i, 0.2) for i in range(8))
    fixture = make_playback(notes, selected={"4"})

    fixture.playback.selectionAudition.playContext()

    assert fixture.player.temporary_notes is not None
    assert [note.id for note in fixture.player.temporary_notes] == ["3", "4", "5"]
    fixture.playback.selectionAudition.setLoopSelection(True)
    fixture.player.finish_temporary()
    qapp.processEvents()

    assert fixture.player.finish_temporary_calls == 1
    assert fixture.player.stop_temporary_calls == 0
    assert fixture.player.temporary_play_calls >= 2


def test_selection_audition_exposes_project_position_for_playhead(qapp) -> None:
    notes = (make_note("selected", 4.0, 60, 1.0),)
    fixture = make_playback(notes, selected={"selected"})

    fixture.playback.selectionAudition.playSelection()
    fixture.player.temporaryPositionChanged.emit(0.35)

    assert fixture.playback.selectionAudition.positionSeconds == pytest.approx(4.35)


def test_loop_restart_ignores_reentrant_completion_from_stopping_old_session(qapp) -> None:
    notes = (make_note("selected", 1.0, 60, 0.2),)
    editor = EditorController(ProjectState(notes, frozenset({"selected"})))
    player = ReentrantFinishPlayer()
    playback = PlaybackController(editor, player)
    audition = playback.selectionAudition

    audition.playSelection()
    audition.setLoopSelection(True)
    player.emit_finish_while_stopping = True
    player.finish_temporary()
    qapp.processEvents()

    assert player.temporary_play_calls == 2
    assert audition.playing is True


def test_normal_play_cancels_a_deferred_loop_restart(qapp) -> None:
    notes = (make_note("selected", 1.0, 60, 0.2),)
    fixture = make_playback(notes, selected={"selected"})
    audition = fixture.playback.selectionAudition

    audition.playSelection()
    audition.setLoopSelection(True)
    fixture.player.finish_temporary()
    fixture.playback.play()
    qapp.processEvents()

    assert fixture.playback.playing is True
    assert audition.playing is False
    assert fixture.player.temporary_play_calls == 1


def test_solo_selection_filters_main_playback_and_restores_full_snapshot(qapp) -> None:
    notes = (
        make_note("a", 0.0, 60, 0.5),
        make_note("b", 1.0, 64, 0.5),
    )
    fixture = make_playback(notes, selected={"a"})

    fixture.playback.selectionAudition.setSoloSelection(True)
    fixture.playback.play()
    assert [note.id for note in fixture.player.played[-1]] == ["a"]
    assert fixture.playback.soloSelection is True
    assert fixture.playback.selectionAudition.soloSelection is True

    fixture.playback.selectionAudition.setSoloSelection(False)
    assert [note.id for note in fixture.player.played[-1]] == ["a", "b"]
    assert fixture.playback.soloSelection is False


def test_emptying_solo_selection_stops_transport_without_project_mutation(qapp) -> None:
    notes = (
        make_note("a", 0.0, 60, 0.5),
        make_note("b", 1.0, 64, 0.5),
    )
    fixture = make_playback(notes, selected={"a"})
    before = fixture.editor.state

    fixture.playback.setSoloSelection(True)
    fixture.playback.play()
    fixture.editor.clearSelection()
    qapp.processEvents()

    assert fixture.playback.playing is False
    assert fixture.player.stop_calls >= 1
    assert fixture.editor.state == before.clear_selection()
    assert fixture.editor.isDirty is False


def test_selection_audition_stops_normal_transport_but_preserves_playhead(qapp) -> None:
    notes = (
        make_note("a", 0.0, 60, 60.0),
        make_note("b", 30.0, 64, 0.5),
    )
    fixture = make_playback(notes, selected={"a"})
    fixture.playback.seek(45.2)
    fixture.playback.play()
    assert fixture.playback.playing is True

    fixture.playback.selectionAudition.playSelection()

    assert fixture.playback.playing is False
    assert fixture.playback.positionSeconds == pytest.approx(45.2)
    assert fixture.player.temporary_notes is not None


def test_100_note_selection_preparation_records_timing(qapp) -> None:
    notes = tuple(
        make_note(f"selection-{index}", index * 0.125, 48 + index % 36, 0.2)
        for index in range(100)
    )
    fixture = make_playback(notes, selected={note.id for note in notes})

    started = perf_counter()
    fixture.playback.selectionAudition.playSelection()
    elapsed = perf_counter() - started

    assert fixture.player.temporary_notes is not None
    assert len(fixture.player.temporary_notes) == 100
    assert fixture.player.temporary_notes[0].start_time == pytest.approx(0.0)
    assert fixture.editor.state.notes == notes
    print(f"selection audition preparation 100 notes: {elapsed:.6f}s")
