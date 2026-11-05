import threading
import wave
from array import array

import pytest
from PySide6.QtCore import QObject, Signal

from ai_piano_studio.audio import controller as audio_controller_module
from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.proposal_manager import MusicEditProposalManager
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway


class FakePlayer(QObject):
    positionChanged = Signal(float)
    playingChanged = Signal(bool)
    playbackFailed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.played: list[tuple[Note, ...]] = []
        self.auditions: list[tuple] = []
        self.seek_positions: list[float] = []
        self.pause_calls = 0
        self.stop_calls = 0
        self.stop_modes: list[ReleaseMode] = []
        self.volume = None
        self.playback_rate = None
        self.released_voices: list[tuple[int, str]] = []
        self.stop_audition_modes: list[ReleaseMode] = []
        self._next_voice_id = 1

    def play_notes(self, notes: tuple[Note, ...]) -> None:
        self.played.append(notes)
        self.playingChanged.emit(True)

    def pause(self) -> None:
        self.pause_calls += 1

    def stop(self, mode: ReleaseMode, *, after_stopped=None) -> None:
        self.stop_calls += 1
        self.stop_modes.append(mode)
        self.positionChanged.emit(0.0)

    def seek(self, seconds: float) -> None:
        self.seek_positions.append(seconds)

    def audition(self, pitch: int, velocity: int = 75, duration: float | None = None) -> int:
        values = (pitch, velocity) if duration is None else (pitch, velocity, duration)
        self.auditions.append(values)
        voice_id = self._next_voice_id
        self._next_voice_id += 1
        return voice_id

    def release_audition(self, voice_id: int, mode: str) -> None:
        self.released_voices.append((voice_id, mode))

    def stop_audition(self, mode: ReleaseMode) -> None:
        self.stop_audition_modes.append(mode)

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def set_playback_rate(self, rate: float) -> None:
        self.playback_rate = rate


class RealtimeTestMixer(QObject):
    voiceFinished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.discarded_voice_id_batches: list[tuple[str, ...]] = []
        self.active_voice_ids: set[str] = set()

    def start_audio(self, *, buffer_milliseconds: int | None = None) -> None:
        return None

    def pause_audio(self) -> None:
        return None

    def resume_audio(self) -> None:
        return None

    def set_volume(self, volume: float) -> None:
        return None

    def discard_voice_ids_and_reset_output(self, voice_ids) -> None:
        discarded = tuple(voice_ids)
        self.discarded_voice_id_batches.append(discarded)
        self.active_voice_ids.difference_update(discarded)

    def emit_voice_finished(self, voice_id: str) -> None:
        self.active_voice_ids.discard(voice_id)
        self.voiceFinished.emit(voice_id)

    def release_all(self, mode: ReleaseMode) -> None:
        return None

    def release_all_and_wait_for_stop(self) -> None:
        return None

    def stop_audio(self) -> None:
        return None


class FinishedSessionResetGuardMixer(RealtimeTestMixer):
    """Record the unsafe empty-session reset used by the old loop path."""

    def __init__(self) -> None:
        super().__init__()
        self.unsafe_empty_reset_calls = 0

    def discard_voice_ids_and_reset_output(self, voice_ids) -> None:
        if not tuple(voice_ids):
            self.unsafe_empty_reset_calls += 1
        super().discard_voice_ids_and_reset_output(voice_ids)


class RealtimeTestSource:
    output_channels = 2

    def __init__(self) -> None:
        self.mixer = RealtimeTestMixer()
        self.started: list[tuple[str, int, int]] = []
        self.released: list[tuple[str, ReleaseMode]] = []

    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> None:
        self.started.append((voice_id, pitch, velocity))
        self.mixer.active_voice_ids.add(voice_id)

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self.released.append((voice_id, mode))

    def release_all(self, mode: ReleaseMode) -> None:
        return None


class RealtimeTestClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def seeded_editor() -> EditorController:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=80)
    return EditorController(ProjectState((note,)))


def test_play_with_notes_sends_snapshot_to_player() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.play()
    assert player.played == [editor.state.notes]


def test_play_uses_effective_music_layer_snapshot() -> None:
    melody = MusicLayer.create(
        id="melody",
        name="主旋律",
        transpose=12,
        velocity_offset=10,
        volume=0.5,
        solo=True,
        order=0,
    )
    backing = MusicLayer.create(id="backing", name="伴奏", muted=True, order=1)
    editor = EditorController(
        ProjectState(
            notes=(
                Note.create(
                    id="lead", pitch=60, start_time=0.0, duration=0.5,
                    velocity=80, layer_id="melody",
                ),
                Note.create(
                    id="back", pitch=48, start_time=0.0, duration=0.5,
                    velocity=70, layer_id="backing",
                ),
            ),
            layers=(melody, backing),
        )
    )
    player = FakePlayer()

    PlaybackController(editor, player).play()

    assert len(player.played) == 1
    assert [(note.id, note.pitch, note.velocity) for note in player.played[0]] == [
        ("lead", 72, 45)
    ]


def test_detached_snapshot_stops_normal_playback_and_ignores_live_editor_refreshes() -> None:
    """Break caught: proposal preview runs beside normal playback or is replaced by a live project edit."""
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    proposal_notes = (Note.create(id="proposal", pitch=72, start_time=0.0, duration=0.5),)
    normal_notes = editor.state.playback_notes

    playback.play()
    playback.play_detached_notes(proposal_notes)
    editor._execute(
        editor.state.add_note(
            Note.create(id="live", pitch=67, start_time=1.0, duration=0.5),
            select=False,
        )
    )

    assert player.stop_calls == 1
    assert player.played == [normal_notes, proposal_notes]
    assert playback.playing is True


def test_playing_refreshes_its_snapshot_after_a_later_note_is_synchronized() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.play()
    player.positionChanged.emit(0.25)

    editor.createNoteAt(800, 144)  # 10.0 seconds, beyond the original 0.5-second end.

    assert [[note.id for note in snapshot] for snapshot in player.played] == [["a"]]
    assert playback.totalSeconds == 0.5

    editor.synchronizeNow()

    assert player.played[-1] == editor.state.playback_notes
    assert player.seek_positions == [0.25]
    assert playback.totalSeconds == 10.5


def test_paused_playback_keeps_its_position_after_notes_change() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.play()
    player.positionChanged.emit(0.25)
    playback.pause()

    editor.createNoteAt(800, 144)
    editor.synchronizeNow()
    playback.play()

    assert player.played[-1] == editor.state.playback_notes
    assert player.seek_positions == [0.25]


def test_realtime_completion_restarts_a_voice_without_a_stale_stop_seek(qapp) -> None:
    """A completed realtime session must restart cleanly through the controller."""
    source = RealtimeTestSource()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    playback = PlaybackController(seeded_editor(), player)

    playback.play()
    first_voice_id = source.started[-1][0]
    clock.advance(0.5)
    player._project_session.process_due_events()
    source.mixer.emit_voice_finished(first_voice_id)

    assert playback.positionSeconds == 0.0

    playback.play()

    assert source.started[-1][0] == "project:2:a"
    assert source.mixer.discarded_voice_id_batches == [()]


def test_selected_loop_audition_does_not_reset_a_finished_audio_session(qapp) -> None:
    """The exact select -> loop -> audition path must restart without sink teardown."""
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=80)
    editor = EditorController(ProjectState((note,), frozenset({"a"})))
    source = RealtimeTestSource()
    source.mixer = FinishedSessionResetGuardMixer()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    playback = PlaybackController(editor, player)
    audition = playback.selectionAudition

    audition.setLoopSelection(True)
    audition.playSelection()

    first_session = player._temporary_session
    assert first_session is not None
    first_voice_id = next(iter(first_session._active_voice_ids))
    first_session._finish_voice(first_voice_id)
    qapp.processEvents()

    assert source.mixer.unsafe_empty_reset_calls == 0
    assert len(source.started) == 2
    assert player._temporary_session is not None
    assert audition.playing is True


def test_replaying_a_finished_selection_does_not_reset_the_audio_output(qapp) -> None:
    """A normal second click after completion must not tear down the sink."""
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=80)
    editor = EditorController(ProjectState((note,), frozenset({"a"})))
    source = RealtimeTestSource()
    source.mixer = FinishedSessionResetGuardMixer()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    playback = PlaybackController(editor, player)

    playback.selectionAudition.playSelection()
    first_session = player._temporary_session
    assert first_session is not None
    first_session._finish_voice(next(iter(first_session._active_voice_ids)))
    qapp.processEvents()

    playback.selectionAudition.playSelection()

    assert source.mixer.unsafe_empty_reset_calls == 0
    assert len(source.started) == 2
    assert playback.selectionAudition.playing is True


def test_realtime_pause_edit_and_play_uses_the_paused_position(qapp) -> None:
    source = RealtimeTestSource()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    editor = seeded_editor()
    playback = PlaybackController(editor, player)

    playback.play()
    clock.advance(0.25)
    player._project_session.process_due_events()
    playback.pause()
    editor.createNoteAt(800, 144)
    editor.synchronizeNow()

    playback.play()

    assert playback.positionSeconds == pytest.approx(0.25)
    assert player.position_seconds() == pytest.approx(0.25)


def test_realtime_stop_resets_the_controller_cursor_before_the_next_play(qapp) -> None:
    source = RealtimeTestSource()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    playback = PlaybackController(seeded_editor(), player)

    playback.play()
    clock.advance(0.25)
    player._project_session.process_due_events()
    playback.stop()

    assert playback.positionSeconds == 0.0


def test_realtime_stop_then_play_starts_a_new_voice_without_stale_stop_discard(
    qapp,
) -> None:
    source = RealtimeTestSource()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    playback = PlaybackController(seeded_editor(), player)

    playback.play()
    playback.stop()
    stopped_voice_ids = source.mixer.discarded_voice_id_batches[-1]

    playback.play()

    assert source.started[-1][0] == "project:3:a"
    assert source.mixer.discarded_voice_id_batches[-1] == ()
    assert source.started[-1][0] not in stopped_voice_ids
    assert source.started[-1][0] in source.mixer.active_voice_ids

    source.mixer.emit_voice_finished(source.started[0][0])

    assert player.is_playing() is True
    assert source.started[-1][0] in source.mixer.active_voice_ids


def test_realtime_completion_manual_seek_then_play_uses_the_manual_position(qapp) -> None:
    source = RealtimeTestSource()
    clock = RealtimeTestClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    editor = seeded_editor()
    playback = PlaybackController(editor, player)

    playback.play()
    first_voice_id = source.started[-1][0]
    clock.advance(0.5)
    player._project_session.process_due_events()
    source.mixer.emit_voice_finished(first_voice_id)
    editor.createNoteAt(80, 144)
    editor.synchronizeNow()
    future_note = editor.state.playback_notes[-1]
    playback.seek(0.5)

    playback.play()
    clock.advance(future_note.start_time - 0.5)
    player._project_session.process_due_events()

    assert playback.positionSeconds == pytest.approx(future_note.start_time)
    assert player.is_playing() is True
    assert source.started[-1][0].endswith(future_note.id)
    assert source.started[-1][0] in source.mixer.active_voice_ids


def test_selecting_a_note_while_playing_does_not_refresh_the_audio_snapshot() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.play()

    editor.selectNote("a")

    assert player.played == [editor.state.notes]
    assert player.seek_positions == []


def test_play_without_notes_emits_feedback(qtbot) -> None:
    playback = PlaybackController(EditorController(ProjectState()), FakePlayer())
    with qtbot.waitSignal(playback.feedbackRequested, timeout=500) as blocker:
        playback.play()
    assert "音符" in blocker.args[0]


def test_play_failure_emits_feedback(qtbot) -> None:
    class BrokenPlayer(FakePlayer):
        def play_notes(self, notes) -> None:
            raise RuntimeError("no audio device")

    playback = PlaybackController(seeded_editor(), BrokenPlayer())
    with qtbot.waitSignal(playback.feedbackRequested, timeout=500) as blocker:
        playback.play()
    assert "播放失败" in blocker.args[0]


def test_pause_and_stop_delegate_to_player() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)
    playback.pause()
    playback.stop()
    assert player.pause_calls == 1
    assert player.stop_calls == 1
    assert player.stop_modes == [ReleaseMode.STOP]
    assert player.stop_audition_modes == [ReleaseMode.STOP]
    assert playback.positionSeconds == 0.0


def test_project_replacement_stops_audition_without_mutating_replacement_state() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.startAudition(67, 90)

    editor.newProject()

    assert player.stop_audition_modes == [ReleaseMode.STOP]
    assert editor.state == ProjectState()


def test_project_switch_stops_realtime_session() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.play()
    player.positionChanged.emit(0.25)

    editor.newProject()

    assert player.stop_modes == [ReleaseMode.STOP]
    assert playback.playing is False
    assert playback.positionSeconds == 0.0
    assert playback.activePitches == []


def test_position_updates_from_player_signal() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)
    player.positionChanged.emit(1.25)
    assert playback.positionSeconds == 1.25


def test_volume_is_clamped_and_forwarded_to_the_player() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)

    playback.setVolume(1.5)

    assert playback.volume == 1.0
    assert player.volume == 1.0


def test_playback_rate_is_clamped_and_forwarded_to_the_player() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)

    playback.setPlaybackRate(1.5)
    assert playback.playbackRate == 1.5
    assert player.playback_rate == 1.5

    playback.setPlaybackRate(0.1)
    assert playback.playbackRate == 0.5
    assert player.playback_rate == 0.5

    playback.setPlaybackRate(3.0)
    assert playback.playbackRate == 2.0
    assert player.playback_rate == 2.0


def test_controller_applies_initial_volume_and_rate_to_player() -> None:
    player = FakePlayer()

    PlaybackController(seeded_editor(), player)

    assert player.volume == pytest.approx(0.65)
    assert player.playback_rate == pytest.approx(1.0)


def test_async_player_failure_emits_visible_feedback(qtbot) -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)

    with qtbot.waitSignal(playback.feedbackRequested, timeout=500) as blocker:
        player.playbackFailed.emit("decoder failed")

    assert "播放失败" in blocker.args[0]


def test_seek_clamps_to_project_duration_and_updates_position() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)

    playback.seek(10.0)

    assert player.seek_positions == [0.5]
    assert playback.positionSeconds == 0.5


def test_export_audio_renders_current_notes(qtbot, tmp_path) -> None:
    playback = PlaybackController(seeded_editor(), FakePlayer())
    target = tmp_path / "song.wav"

    with qtbot.waitSignal(playback.audioExported, timeout=2000):
        playback.exportAudio(str(target))

    payload = target.read_bytes()
    assert payload[:4] == b"RIFF"
    assert int.from_bytes(payload[4:8], "little") == len(payload) - 8
    assert payload[8:12] == b"WAVE"
    with wave.open(str(target), "rb") as rendered:
        assert rendered.getnchannels() == 1
        assert rendered.getsampwidth() == 2
        assert rendered.getframerate() == 44_100
        assert rendered.getnframes() > 0
        assert len(rendered.readframes(rendered.getnframes())) == rendered.getnframes() * 2


def test_pending_proposal_is_excluded_from_wav_export(qtbot, tmp_path, monkeypatch) -> None:
    """Break caught: WAV rendering consumes a suggestion snapshot before the user applies it."""
    editor = seeded_editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 1.0, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    MusicEditProposalManager(editor, gateway).register(context)
    rendered: list[tuple[Note, ...]] = []

    def capture_render(notes, *, profile):
        rendered.append(tuple(notes))
        return array("h", [0])

    monkeypatch.setattr(audio_controller_module, "render_project", capture_render)
    playback = PlaybackController(editor, FakePlayer())
    target = tmp_path / "pending.wav"

    with qtbot.waitSignal(playback.audioExported, timeout=2000):
        playback.exportAudio(str(target))

    assert rendered == [editor.state.playback_notes]
    assert {note.pitch for note in rendered[0]} == {60}


def test_export_contains_release_after_logical_end_without_extending_ui_duration(qtbot, tmp_path) -> None:
    playback = PlaybackController(seeded_editor(), FakePlayer())
    logical_duration = playback.totalSeconds
    target = tmp_path / "release.wav"

    with qtbot.waitSignal(playback.audioExported, timeout=2000):
        playback.exportAudio(str(target))

    with wave.open(str(target), "rb") as wav:
        rendered_duration = wav.getnframes() / wav.getframerate()
    assert rendered_duration > logical_duration
    assert playback.totalSeconds == logical_duration


def test_export_audio_runs_off_the_ui_thread(qtbot, tmp_path, monkeypatch) -> None:
    playback = PlaybackController(seeded_editor(), FakePlayer())
    target = tmp_path / "song.wav"
    started = threading.Event()
    release = threading.Event()
    profiles = []

    def render_slowly(_notes, *, profile):
        profiles.append(profile)
        started.set()
        release.wait(timeout=1)
        return array("h", [0])

    monkeypatch.setattr(audio_controller_module, "render_project", render_slowly)

    with qtbot.waitSignal(playback.audioExported, timeout=2000):
        playback.exportAudio(str(target))
        assert started.wait(timeout=1)
        assert playback.audioExporting is True
        release.set()

    assert playback.audioExporting is False
    assert profiles == [playback.piano_profile]


def test_export_snapshots_injected_piano_source_before_background_render(
    qtbot, tmp_path
) -> None:
    """Fails if export observes a later slider commit or bypasses the common source."""
    rendered_positions = []

    class Snapshot:
        output_channels = 2

        def __init__(self, position: float) -> None:
            self.position = position

        def render_project(self, _notes):
            rendered_positions.append(self.position)
            return array("h", [0, 0])

    class Source:
        position = 0.25

        def __init__(self) -> None:
            self.started = []

        def start_voice(self, *voice) -> None:
            self.started.append(voice)

        def snapshot_renderer(self):
            return Snapshot(self.position)

    source = Source()
    playback = PlaybackController(seeded_editor(), FakePlayer(), piano_source=source)
    target = tmp_path / "tone-snapshot.wav"

    with qtbot.waitSignal(playback.audioExported, timeout=2000):
        playback.exportAudio(str(target))
        source.position = 0.9

    assert rendered_positions == [0.25]
    assert source.started == []
    with wave.open(str(target), "rb") as rendered_wav:
        assert rendered_wav.getnchannels() == 2


def test_selecting_single_note_auditions_pitch_velocity_and_duration() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    editor.selectNote("a")
    assert player.auditions == [(60, 80, 0.5)]
    assert playback.playing is False


def test_creating_a_note_auditions_pitch_velocity_and_duration() -> None:
    editor = EditorController(ProjectState())
    player = FakePlayer()
    playback = PlaybackController(editor, player)

    editor.beginDrawNoteAt(80, 144, 1.0, 96)

    assert playback is not None
    assert player.auditions == [(64, 96, 1.0)]


def test_multi_selection_does_not_audition() -> None:
    first = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    second = Note.create(id="b", pitch=64, start_time=1.0, duration=0.5)
    editor = EditorController(ProjectState((first, second)))
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    editor.selectNote("a")
    editor.selectNote("b", True)
    assert player.auditions == [(60, 75, 0.5)]


def test_drag_preview_is_grid_by_default() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)

    editor.selectNote("a")
    editor.beginGesture()
    editor.previewMovePixels(20, 0)
    editor.previewMovePixels(20, 0)
    editor.commitGesture()

    assert player.auditions == [(60, 80, 0.5), (60, 80)]


def test_grid_drag_preview_auditions_each_new_pitch_once() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.setDragPreviewMode("grid")

    editor.selectNote("a")
    editor.beginGesture()
    editor.previewMovePixels(0, -18)
    editor.previewMovePixels(0, 0)
    assert player.auditions == [(60, 80, 0.5), (61, 80)]
    editor.commitGesture()

    assert player.auditions == [(60, 80, 0.5), (61, 80)]


def test_grid_drag_preview_releases_the_displaced_voice_safely() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    playback.setDragPreviewMode("grid")

    editor.gesturePreviewRequested.emit(60, 80)
    editor.gesturePreviewRequested.emit(62, 80)

    assert player.auditions == [(60, 80), (62, 80)]
    assert player.released_voices == [(1, "safe")]


def test_audition_note_slot_forwards_to_player() -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)
    playback.auditionNote(67, 90)
    assert player.auditions == [(67, 90)]


def test_selection_preview_releases_the_exact_voice_naturally(qtbot) -> None:
    player = FakePlayer()
    playback = PlaybackController(seeded_editor(), player)

    playback.auditionNoteWithDuration(67, 90, 0.08)
    first_voice_id = 1
    playback.stopAudition("safe")
    playback.auditionNoteWithDuration(67, 90, 0.08)

    qtbot.waitUntil(
        lambda: player.released_voices == [(2, "natural")], timeout=500
    )

    assert first_voice_id not in [voice_id for voice_id, _mode in player.released_voices]
    assert player.stop_audition_modes == [ReleaseMode.SAFE]


def test_total_seconds_tracks_last_note_end() -> None:
    editor = seeded_editor()
    playback = PlaybackController(editor, FakePlayer())
    assert playback.totalSeconds == 0.5
    editor.beginGesture()
    editor.previewAddNote(60, 2.0, 1.0, 80)
    editor.commitGesture()
    assert playback.totalSeconds == 0.5
    editor.synchronizeNow()
    assert playback.totalSeconds == 3.0


def test_duration_changed_signal_on_note_add(qtbot) -> None:
    editor = seeded_editor()
    playback = PlaybackController(editor, FakePlayer())
    editor.beginGesture()
    editor.previewAddNote(60, 2.0, 1.0, 80)
    editor.commitGesture()
    with qtbot.waitSignal(playback.durationChanged, timeout=500):
        editor.synchronizeNow()
    assert playback.totalSeconds == 3.0


def test_active_pitches_follow_playback_position() -> None:
    first = Note.create(id="a", pitch=60, start_time=0.0, duration=1.0)
    second = Note.create(id="b", pitch=64, start_time=0.5, duration=1.0)
    editor = EditorController(ProjectState((first, second)))
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    assert playback.activePitches == []
    player.positionChanged.emit(0.25)
    assert playback.activePitches == [60]
    player.positionChanged.emit(0.75)
    assert playback.activePitches == [60, 64]
    player.positionChanged.emit(1.25)
    assert playback.activePitches == [64]
    player.positionChanged.emit(2.0)
    assert playback.activePitches == []


def test_active_pitches_changed_signal(qtbot) -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    with qtbot.waitSignal(playback.activePitchesChanged, timeout=500):
        player.positionChanged.emit(0.25)
    assert playback.activePitches == [60]


def test_visual_metrics_follow_playback_density_velocity_and_register() -> None:
    sparse_notes = (
        Note.create(id="low", pitch=40, start_time=0.0, duration=1.0, velocity=48),
    )
    dense_notes = sparse_notes + tuple(
        Note.create(
            id=f"high-{index}",
            pitch=76 + index,
            start_time=0.0,
            duration=1.0,
            velocity=112,
        )
        for index in range(4)
    )

    sparse_player = FakePlayer()
    sparse = PlaybackController(EditorController(ProjectState(sparse_notes)), sparse_player)
    sparse_player.playingChanged.emit(True)
    sparse_player.positionChanged.emit(0.25)

    dense_player = FakePlayer()
    dense = PlaybackController(EditorController(ProjectState(dense_notes)), dense_player)
    dense_player.playingChanged.emit(True)
    dense_player.positionChanged.emit(0.25)

    assert dense.visualNoteDensity > sparse.visualNoteDensity
    assert dense.visualEnergy > sparse.visualEnergy
    assert dense.visualPitchBand > sparse.visualPitchBand


def test_active_pitches_refresh_when_notes_change() -> None:
    editor = seeded_editor()
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    player.positionChanged.emit(0.25)
    assert playback.activePitches == [60]
    editor.selectNote("a")
    editor.deleteSelected()
    assert playback.activePitches == [60]
    editor.synchronizeNow()
    assert playback.activePitches == []


def test_detached_preview_exposes_its_own_duration_and_notes_without_editing():
    editor = EditorController(ProjectState())
    player = FakePlayer()
    playback = PlaybackController(editor, player)
    notes = (Note.create(pitch=64, start_time=0, duration=20),)
    playback.play_detached_notes(notes)
    assert playback.totalSeconds == 20
    assert playback.detachedPreviewNotes[0]['pitch'] == 64
    player.positionChanged.emit(12.0)
    assert playback.positionSeconds == 12 and playback.totalSeconds == 20
    assert not editor.state.notes and not editor.isDirty
    playback.stop_detached_preview()
    assert playback.totalSeconds == 0 and playback.detachedPreviewNotes == []
