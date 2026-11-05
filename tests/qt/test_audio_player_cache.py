from array import array
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, sleep
import threading

import pytest
from PySide6.QtCore import QObject, QThreadPool, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer as QtMediaPlayer

from ai_piano_studio.audio import player as player_module
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, ReleaseMode
from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.domain.note import Note


@dataclass(frozen=True)
class FakeDevice:
    name: str
    null: bool = False

    @classmethod
    def null_device(cls):
        return cls("", True)

    def isNull(self) -> bool:
        return self.null

    def description(self) -> str:
        return self.name


class FakeAudioOutput:
    initial_device = FakeDevice.null_device()
    latest = None

    def __init__(self, _parent=None) -> None:
        self._device = self.initial_device
        self.volume = None
        type(self).latest = self

    def device(self):
        return self._device

    def setDevice(self, device) -> None:
        self._device = device

    def setVolume(self, volume: float) -> None:
        self.volume = volume


class FakeMediaPlayer(QObject):
    PlaybackState = QtMediaPlayer.PlaybackState
    MediaStatus = QtMediaPlayer.MediaStatus
    Error = QtMediaPlayer.Error

    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, str)
    latest = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.audio_output = None
        self.position_ms = 0
        self.state = self.PlaybackState.StoppedState
        self.status = self.MediaStatus.LoadedMedia
        self.source_url = QUrl()
        self.play_calls = 0
        self.stop_calls = 0
        self.playback_rate = None
        type(self).latest = self

    def setAudioOutput(self, output) -> None:
        self.audio_output = output

    def playbackState(self):
        return self.state

    def mediaStatus(self):
        return self.status

    def position(self) -> int:
        return self.position_ms

    def setPosition(self, position_ms: int) -> None:
        self.position_ms = position_ms

    def setSource(self, source: QUrl) -> None:
        self.source_url = source

    def source(self) -> QUrl:
        return self.source_url

    def play(self) -> None:
        self.play_calls += 1
        self.state = self.PlaybackState.PlayingState

    def pause(self) -> None:
        self.state = self.PlaybackState.PausedState

    def stop(self) -> None:
        self.stop_calls += 1
        self.state = self.PlaybackState.StoppedState

    def setPlaybackRate(self, rate: float) -> None:
        self.playback_rate = rate


class FakeMediaDevices(QObject):
    audioOutputsChanged = Signal()
    current = FakeDevice.null_device()
    latest = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        type(self).latest = self

    @classmethod
    def defaultAudioOutput(cls):
        return cls.current

    def change_default(self, device: FakeDevice) -> None:
        type(self).current = device
        self.audioOutputsChanged.emit()


class RecordingVoicePool(QObject):
    latest = None

    def __init__(self, mixer, parent=None) -> None:
        super().__init__(parent)
        self.mixer = mixer
        self.started: list[tuple[int, int, tuple[int, ...], int, str]] = []
        self.released: list[tuple[int, ReleaseMode]] = []
        self.release_all_modes: list[ReleaseMode] = []
        self.dispose_calls = 0
        self.buffers_existed_on_dispose: bool | None = None
        self._next_voice_id = 41
        type(self).latest = self

    def start(
        self,
        pitch: int,
        velocity: int,
        source,
        *,
        source_sample_rate: int,
        owner_tag: str,
    ) -> int:
        self.started.append(
            (pitch, velocity, source, source_sample_rate, owner_tag)
        )
        voice_id = self._next_voice_id
        self._next_voice_id += 1
        return voice_id

    def release(self, voice_id: int, mode: ReleaseMode) -> None:
        self.released.append((voice_id, mode))

    def release_all(self, mode: ReleaseMode) -> None:
        self.release_all_modes.append(mode)

    def dispose(self) -> None:
        self.dispose_calls += 1
        self.buffers_existed_on_dispose = bool(self.started) and all(
            isinstance(source, tuple) and bool(source)
            for _, _, source, _, _ in self.started
        )


class RecordingRealtimeMixer(QObject):
    voiceFinished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started_audio_with: list[int] = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.volumes: list[float] = []
        self.discarded_voice_id_batches: list[tuple[str, ...]] = []

    def start_audio(self, *, buffer_milliseconds: int) -> None:
        self.started_audio_with.append(buffer_milliseconds)

    def pause_audio(self) -> None:
        self.pause_calls += 1

    def resume_audio(self) -> None:
        self.resume_calls += 1

    def set_volume(self, volume: float) -> None:
        self.volumes.append(volume)

    def release_all(self, _mode: ReleaseMode) -> None:
        pass

    def discard_voice_ids_and_reset_output(self, voice_ids) -> None:
        self.discarded_voice_id_batches.append(tuple(voice_ids))

    def stop_audio(self) -> None:
        pass


class RecordingRealtimeSource:
    output_channels = 2

    def __init__(self) -> None:
        self.mixer = RecordingRealtimeMixer()
        self.started: list[tuple[str, int, int]] = []
        self.released: list[tuple[str, ReleaseMode]] = []

    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> None:
        self.started.append((voice_id, pitch, velocity))

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self.released.append((voice_id, mode))

    def release_all(self, _mode: ReleaseMode) -> None:
        pass

    def snapshot_renderer(self):
        return self

    def render_project(self, _notes):
        return array("h", [0, 0])


def project_note(identifier: str, start_time: float) -> Note:
    return Note.create(
        id=identifier,
        pitch=60,
        start_time=start_time,
        duration=1.0,
        velocity=90,
    )


def sixty_second_notes() -> tuple[Note, ...]:
    return tuple(project_note(str(index), float(index)) for index in range(60))


def install_fake_multimedia(
    monkeypatch,
    *,
    default_device: FakeDevice,
    output_device: FakeDevice | None = None,
) -> None:
    FakeAudioOutput.initial_device = output_device or FakeDevice.null_device()
    FakeAudioOutput.latest = None
    FakeMediaPlayer.latest = None
    FakeMediaDevices.current = default_device
    FakeMediaDevices.latest = None
    monkeypatch.setattr(player_module, "QAudioOutput", FakeAudioOutput)
    monkeypatch.setattr(player_module, "QMediaPlayer", FakeMediaPlayer)
    monkeypatch.setattr(player_module, "QMediaDevices", FakeMediaDevices, raising=False)


def install_fake_renderer(monkeypatch) -> None:
    monkeypatch.setattr(player_module, "render_project", lambda _notes: object())

    def write_fake_wav(path, _samples):
        path.write_bytes(b"RIFF")
        return path

    monkeypatch.setattr(player_module, "write_wav", write_fake_wav)


def test_realtime_project_playback_does_not_wait_for_full_song_render(
    qapp, monkeypatch
) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    source = RecordingRealtimeSource()
    player = AudioPlayer(piano_source=source)
    monkeypatch.setattr(
        player_module,
        "_ProjectRenderJob",
        lambda *_args: pytest.fail("offline render queued"),
    )

    player.play_notes(sixty_second_notes())

    assert source.started[0][0].startswith("project:")
    assert player._project_session.parent() is player
    assert player._project_render_jobs == set()
    player.stop()


def test_project_switch_invalidates_old_audio_stream(qapp, monkeypatch) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    source = RecordingRealtimeSource()
    player = AudioPlayer(piano_source=source)

    player.play_notes((project_note("old", 0.0),))
    player.play_notes((project_note("new", 0.0),))

    assert ("project:1:old", ReleaseMode.STOP) in source.released
    assert ("project:1:old",) in source.mixer.discarded_voice_id_batches
    assert source.started[-1][0] == "project:2:new"
    player.stop()


def test_realtime_transport_controls_use_the_owned_streaming_session(
    qapp, monkeypatch
) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    source = RecordingRealtimeSource()
    clock = FakeClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    notes = (project_note("held", 0.0),)

    player.set_volume(0.35)
    player.set_playback_rate(2.0)
    player.play_notes(notes)
    clock.advance(0.25)

    assert player.is_playing() is True
    assert player.position_seconds() == pytest.approx(0.5)
    assert source.mixer.volumes[-1] == pytest.approx(0.35)
    player.pause()
    assert player.is_playing() is False
    player.play_notes(notes)
    assert source.mixer.resume_calls == 1

    stopped = []
    player.stop(ReleaseMode.STOP, after_stopped=lambda: stopped.append(True))

    assert player.is_playing() is False
    assert stopped == [True]


def test_audition_cache_evicts_the_least_recently_used_buffer(qapp, monkeypatch) -> None:
    monkeypatch.setattr(player_module, "MAX_AUDITION_CACHE_ENTRIES", 2)
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)
    monkeypatch.setattr(player_module, "render_note", lambda pitch, *_args: (pitch,))
    player = AudioPlayer()

    player.audition(60, 75)
    first_buffer = next(iter(player._audition_cache.values()))
    player.audition(62, 75)
    player.audition(64, 75)

    assert list(player._audition_cache) == [(62, 75, 4000), (64, 75, 4000)]
    assert first_buffer not in player._audition_cache.values()
    assert RecordingVoicePool.latest.started[0][2] is first_buffer


def test_cache_eviction_does_not_mutate_a_buffer_held_by_an_active_voice(qapp, monkeypatch) -> None:
    monkeypatch.setattr(player_module, "MAX_AUDITION_CACHE_ENTRIES", 1)
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)
    monkeypatch.setattr(player_module, "render_note", lambda pitch, *_args: [pitch])
    player = AudioPlayer()

    player.audition(60, 75)
    first_buffer = next(iter(player._audition_cache.values()))
    player.audition(62, 75)

    assert first_buffer == (60,)
    assert first_buffer not in player._audition_cache.values()
    assert RecordingVoicePool.latest.started[0][2] is first_buffer


class FakeClock:
    def __init__(self) -> None:
        self._seconds = 0.0

    def __call__(self) -> float:
        return self._seconds

    def advance(self, seconds: float) -> None:
        self._seconds += seconds


def make_playing_player(monkeypatch):
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    clock = FakeClock()
    player = AudioPlayer(clock=clock)
    player.set_volume(0.35)
    media = FakeMediaPlayer.latest
    media.state = QtMediaPlayer.PlaybackState.PlayingState
    media.position_ms = 1250
    return player, media, FakeAudioOutput.latest, clock


def test_transport_stop_fades_before_media_stop_and_then_resets(qapp, monkeypatch) -> None:
    player, media, output, clock = make_playing_player(monkeypatch)

    player.stop(ReleaseMode.STOP)

    assert media.stop_calls == 0
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds / 2)
    player.process_media_fade()
    assert 0.0 < output.volume < player.volume
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds)
    player.process_media_fade()
    assert media.stop_calls == 1
    assert media.position_ms == 0
    assert output.volume == pytest.approx(player.volume)


def test_repeated_transport_stop_completes_only_one_media_stop(qapp, monkeypatch) -> None:
    player, media, _output, clock = make_playing_player(monkeypatch)

    player.stop(ReleaseMode.STOP)
    player.stop(ReleaseMode.STOP)
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds)
    player.process_media_fade()

    assert media.stop_calls == 1


def test_stop_escalation_does_not_raise_a_safe_fade_gain(qapp, monkeypatch) -> None:
    player, media, output, clock = make_playing_player(monkeypatch)

    player.stop(ReleaseMode.SAFE)
    clock.advance(ACOUSTIC_PIANO_V2.safe_release_seconds / 2)
    player.process_media_fade()
    safe_fade_gain = output.volume

    player.stop(ReleaseMode.STOP)

    assert output.volume == pytest.approx(safe_fade_gain)
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds)
    player.process_media_fade()
    assert media.stop_calls == 1


def test_master_volume_change_scales_an_active_media_fade(qapp, monkeypatch) -> None:
    player, _media, output, clock = make_playing_player(monkeypatch)

    player.stop(ReleaseMode.STOP)
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds / 2)
    player.process_media_fade()
    player.set_volume(0.1)

    assert output.volume == pytest.approx(0.05)


def test_unmuting_scales_an_active_fade_started_at_zero_volume(qapp, monkeypatch) -> None:
    player, _media, output, clock = make_playing_player(monkeypatch)
    player.set_volume(0.0)

    player.stop(ReleaseMode.SAFE)
    clock.advance(ACOUSTIC_PIANO_V2.safe_release_seconds / 2)
    player.process_media_fade()
    player.set_volume(0.2)

    assert output.volume == pytest.approx(0.1)


def test_stop_after_completed_fade_is_idempotent_and_delivers_callback(qapp, monkeypatch) -> None:
    player, media, _output, clock = make_playing_player(monkeypatch)
    completed: list[str] = []

    player.stop(ReleaseMode.STOP)
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds)
    player.process_media_fade()
    player.stop(ReleaseMode.STOP, after_stopped=lambda: completed.append("done"))

    assert media.stop_calls == 1
    assert completed == ["done"]


def test_new_project_play_cancels_an_obsolete_stop_callback(qapp, monkeypatch) -> None:
    player, media, _output, clock = make_playing_player(monkeypatch)
    install_fake_renderer(monkeypatch)
    completed: list[str] = []
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    player.stop(ReleaseMode.STOP, after_stopped=lambda: completed.append("stopped"))
    player.play_notes(notes)
    clock.advance(ACOUSTIC_PIANO_V2.stop_fade_seconds)
    player.process_media_fade()

    assert media.stop_calls == 0
    assert completed == []


def test_audition_cache_does_not_reuse_pcm_with_a_different_duration(qapp, monkeypatch) -> None:
    rendered: list[float] = []
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)

    def render_fake_note(_pitch: int, duration: float, _velocity: int, **_kwargs):
        rendered.append(duration)
        return (1,)

    monkeypatch.setattr(player_module, "render_note", render_fake_note)

    player = AudioPlayer()

    player.audition(60, 75, 0.08)
    player.audition(60, 75, 4.0)

    assert rendered == [0.08, 4.0]
    assert list(player._audition_cache) == [(60, 75, 80), (60, 75, 4000)]


def test_none_duration_renders_the_profile_held_voice_length(qapp, monkeypatch) -> None:
    rendered: list[float] = []
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)

    def render_fake_note(_pitch: int, duration: float, _velocity: int, **_kwargs):
        rendered.append(duration)
        return (1,)

    monkeypatch.setattr(player_module, "render_note", render_fake_note)

    player = AudioPlayer()

    player.audition(60, 75)

    assert rendered == [ACOUSTIC_PIANO_V2.render_hold_seconds]
    assert ACOUSTIC_PIANO_V2.render_hold_seconds == 4.0


def test_audition_cache_reuses_only_the_same_pitch_and_velocity(qapp, monkeypatch) -> None:
    rendered: list[tuple[int, int]] = []

    def render_fake_note(pitch: int, _duration: float, velocity: int, **_kwargs):
        rendered.append((pitch, velocity))
        return (1,)

    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)
    monkeypatch.setattr(player_module, "render_note", render_fake_note)

    player = AudioPlayer()

    first_voice = player.audition(60, 75)
    repeated_voice = player.audition(60, 75)
    changed_velocity_voice = player.audition(60, 90)

    assert first_voice != repeated_voice != changed_velocity_voice
    assert rendered == [(60, 75), (60, 90)]
    assert list(player._audition_cache) == [(60, 75, 4000), (60, 90, 4000)]


def test_audition_voice_api_delegates_release_modes(qapp, monkeypatch) -> None:
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)
    monkeypatch.setattr(player_module, "render_note", lambda *_args: (1,))
    player = AudioPlayer()

    voice_id = player.audition(60, 90)
    player.release_audition(voice_id, ReleaseMode.NATURAL)
    player.stop_audition()

    assert voice_id == 41
    assert RecordingVoicePool.latest.started[0][:2] == (60, 90)
    assert RecordingVoicePool.latest.released == [(41, ReleaseMode.NATURAL)]
    assert RecordingVoicePool.latest.release_all_modes == [ReleaseMode.STOP]


def test_player_destruction_disposes_its_voice_pool(qapp, qtbot, monkeypatch) -> None:
    monkeypatch.setattr(player_module, "RealtimeVoicePool", RecordingVoicePool)
    monkeypatch.setattr(player_module, "render_note", lambda *_args: (1,))
    player = AudioPlayer()
    pool = RecordingVoicePool.latest
    player.audition(60, 75)
    temp_dir = player._temp_dir

    assert pool.started[0][2] == (1,)

    with qtbot.waitSignal(player.destroyed, timeout=500):
        player.deleteLater()

    qtbot.waitUntil(lambda: pool.dispose_calls == 1, timeout=500)
    assert pool.buffers_existed_on_dispose is True
    qtbot.waitUntil(lambda: not temp_dir.exists(), timeout=500)


def test_output_change_after_media_destruction_does_not_touch_deleted_media(
    qapp, qtbot, monkeypatch
) -> None:
    """A device-change signal can race with Qt deleting the media child."""
    speakers = FakeDevice("speakers")
    headphones = FakeDevice("headphones")
    install_fake_multimedia(
        monkeypatch,
        default_device=speakers,
        output_device=speakers,
    )
    player = AudioPlayer()

    with qtbot.waitSignal(player._media.destroyed, timeout=500):
        player._media.deleteLater()

    FakeMediaDevices.latest.change_default(headphones)

    assert FakeAudioOutput.latest.device() == speakers


def test_completed_project_render_is_ignored_after_media_destruction(
    qapp, qtbot, monkeypatch
) -> None:
    render_written = threading.Event()
    allow_completion = threading.Event()

    def write_render_then_wait(path, _samples):
        path.write_bytes(b"RIFF")
        render_written.set()
        assert allow_completion.wait(timeout=1)
        return path

    monkeypatch.setattr(player_module, "render_project", lambda _notes: array("h", [0]))
    monkeypatch.setattr(player_module, "write_wav", write_render_then_wait)
    player = AudioPlayer()
    monkeypatch.setattr(player, "_bind_default_output", lambda: None)
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    player.play_notes(notes)
    qtbot.waitUntil(render_written.is_set, timeout=500)
    with qtbot.waitSignal(player._media.destroyed, timeout=500):
        player._media.deleteLater()

    allow_completion.set()
    assert QThreadPool.globalInstance().waitForDone(1000)
    qapp.processEvents()


def test_failed_project_render_is_ignored_after_media_destruction(
    qapp, qtbot, monkeypatch
) -> None:
    render_started = threading.Event()
    allow_failure = threading.Event()

    def fail_after_media_destruction(_path, _samples):
        render_started.set()
        assert allow_failure.wait(timeout=1)
        raise RuntimeError("renderer failed")

    monkeypatch.setattr(player_module, "render_project", lambda _notes: array("h", [0]))
    monkeypatch.setattr(player_module, "write_wav", fail_after_media_destruction)
    player = AudioPlayer()
    monkeypatch.setattr(player, "_bind_default_output", lambda: None)
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    player.play_notes(notes)
    qtbot.waitUntil(render_started.is_set, timeout=500)
    with qtbot.waitSignal(player._media.destroyed, timeout=500):
        player._media.deleteLater()

    allow_failure.set()
    assert QThreadPool.globalInstance().waitForDone(1000)
    qapp.processEvents()


def test_project_render_job_cancels_an_active_cancellable_renderer(
    qapp, qtbot, monkeypatch, tmp_path
) -> None:
    class CancellableRenderer:
        selection = "test"
        output_channels = 1

        def __init__(self) -> None:
            self.started = threading.Event()
            self.cancelled = threading.Event()
            self.release_legacy_render = threading.Event()

        def render_project(self, _notes):
            self.started.set()
            assert self.release_legacy_render.wait(timeout=1)
            return array("h", [0])

        def render_project_cancellable(self, _notes, should_cancel):
            self.started.set()
            while not should_cancel():
                sleep(0.005)
            self.cancelled.set()
            return array("h")

    renderer = CancellableRenderer()
    monkeypatch.setattr(player_module, "write_wav", lambda path, _samples, **_kwargs: path)
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)
    job = player_module._ProjectRenderJob(
        1, tmp_path / "cancelled-project.wav", notes, renderer
    )

    try:
        QThreadPool.globalInstance().start(job)
        qtbot.waitUntil(renderer.started.is_set, timeout=500)
        job.cancel()
        qtbot.waitUntil(renderer.cancelled.is_set, timeout=500)
    finally:
        renderer.release_legacy_render.set()
        assert QThreadPool.globalInstance().waitForDone(1000)


def test_stopping_project_playback_cancels_the_default_renderer(
    qapp, qtbot, monkeypatch
) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    render_started = threading.Event()
    render_cancelled = threading.Event()
    release_legacy_render = threading.Event()

    def render_until_cancelled(_notes, *, should_cancel=None):
        render_started.set()
        while should_cancel is None or not should_cancel():
            if release_legacy_render.wait(timeout=0.005):
                return array("h", [0])
        render_cancelled.set()
        return array("h")

    monkeypatch.setattr(player_module, "render_project", render_until_cancelled)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    try:
        player.play_notes(notes)
        qtbot.waitUntil(render_started.is_set, timeout=500)
        player.stop()
        qtbot.waitUntil(render_cancelled.is_set, timeout=500)
    finally:
        release_legacy_render.set()
        assert QThreadPool.globalInstance().waitForDone(1000)


def test_project_snapshot_changes_use_a_new_media_url(qapp, qtbot, monkeypatch) -> None:
    # This tests URL identity, not device playback; never hand fake RIFF to Qt.
    device = FakeDevice('snapshot-test')
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    monkeypatch.setattr(player_module, "render_project", lambda _notes: object())

    def write_fake_wav(path, _samples):
        path.write_bytes(b"RIFF")
        return path

    monkeypatch.setattr(player_module, "write_wav", write_fake_wav)
    player = AudioPlayer()
    first = (Note.create(id="first", pitch=60, start_time=0.0, duration=0.2),)
    second = (Note.create(id="second", pitch=64, start_time=3.0, duration=0.2),)

    player.play_notes(first)
    qtbot.waitUntil(lambda: bool(player._media.source().toString()), timeout=500)
    first_url = player._media.source().toString()
    player.play_notes(second)
    qtbot.waitUntil(
        lambda: player._media.source().toString() != first_url, timeout=500
    )

    assert player._media.source().toString() != first_url


def test_project_rendering_does_not_block_the_gui_thread(qapp, qtbot, monkeypatch) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    started = threading.Event()
    release = threading.Event()
    rendered_on: list[int] = []

    def render_slowly(_notes):
        rendered_on.append(threading.get_ident())
        started.set()
        release.wait(timeout=1)
        return array("h", [0])

    monkeypatch.setattr(player_module, "render_project", render_slowly)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    started_at = perf_counter()
    player.play_notes(notes)
    elapsed = perf_counter() - started_at

    assert elapsed < 0.1
    qtbot.waitUntil(started.is_set, timeout=500)
    assert rendered_on != [threading.get_ident()]
    assert FakeMediaPlayer.latest.play_calls == 0
    release.set()
    qtbot.waitUntil(lambda: FakeMediaPlayer.latest.play_calls == 1, timeout=1000)


def test_project_playback_resume_uses_latest_media_position(qapp, qtbot, monkeypatch) -> None:
    device = FakeDevice("speakers")
    install_fake_multimedia(monkeypatch, default_device=device, output_device=device)
    install_fake_renderer(monkeypatch)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    player.play_notes(notes)
    qtbot.waitUntil(lambda: FakeMediaPlayer.latest.play_calls == 1, timeout=500)
    FakeMediaPlayer.latest.position_ms = 5800
    player._poll_position()
    player.pause()
    player.play_notes(notes)

    assert FakeMediaPlayer.latest.position_ms == 5800


def test_project_playback_rebinds_to_the_current_default_output(qapp, qtbot, monkeypatch) -> None:
    old = FakeDevice("old speakers")
    current = FakeDevice("headphones")
    install_fake_multimedia(
        monkeypatch,
        default_device=current,
        output_device=old,
    )
    install_fake_renderer(monkeypatch)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    player.play_notes(notes)

    assert FakeAudioOutput.latest.device() == current
    qtbot.waitUntil(lambda: FakeMediaPlayer.latest.play_calls == 1, timeout=500)


def test_null_default_output_rejects_project_playback(qapp, monkeypatch) -> None:
    install_fake_multimedia(
        monkeypatch,
        default_device=FakeDevice.null_device(),
    )
    install_fake_renderer(monkeypatch)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)

    with pytest.raises(RuntimeError, match="audio output"):
        player.play_notes(notes)


def test_default_output_change_preserves_position_and_playing_state(qapp, monkeypatch) -> None:
    speakers = FakeDevice("speakers")
    headphones = FakeDevice("headphones")
    install_fake_multimedia(
        monkeypatch,
        default_device=speakers,
        output_device=speakers,
    )
    player = AudioPlayer()
    FakeMediaPlayer.latest.position_ms = 2410
    FakeMediaPlayer.latest.state = QtMediaPlayer.PlaybackState.PlayingState

    FakeMediaDevices.latest.change_default(headphones)

    assert FakeAudioOutput.latest.device() == headphones
    assert FakeMediaPlayer.latest.position_ms == 2410
    assert FakeMediaPlayer.latest.play_calls == 1


def test_media_error_is_logged_and_forwarded(qapp, qtbot, caplog, monkeypatch) -> None:
    headphones = FakeDevice("headphones")
    install_fake_multimedia(
        monkeypatch,
        default_device=headphones,
    )
    install_fake_renderer(monkeypatch)
    player = AudioPlayer()
    notes = (Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),)
    player.play_notes(notes)

    with qtbot.waitSignal(player.playbackFailed, timeout=500) as blocker:
        FakeMediaPlayer.latest.errorOccurred.emit(
            QtMediaPlayer.Error.ResourceError,
            "decoder failed",
        )

    assert blocker.args == ["decoder failed"]
    assert "headphones" in caplog.text
    assert "decoder failed" in caplog.text
