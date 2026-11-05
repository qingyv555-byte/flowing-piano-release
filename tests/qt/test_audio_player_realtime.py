from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from pathlib import Path
import wave

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from ai_piano_studio.audio import mixer as mixer_module
from ai_piano_studio.audio.controller import PlaybackController
from ai_piano_studio.audio import player as player_module
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.player import AudioPlayer
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.realtime import RealtimeVoicePool
from ai_piano_studio.audio.sample_piano import SamplePianoEngine, SampleVoice
from ai_piano_studio.audio.source import (
    ContinuousPianoToneSource,
    LegacyPianoSource,
)
from ai_piano_studio.audio.synth import SAMPLE_RATE
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController


@dataclass
class FakeMixer:
    started: list[tuple[str, int, int, tuple[int, ...], int, str]] = field(
        default_factory=list
    )
    note_offs: list[tuple[str, ReleaseMode]] = field(default_factory=list)
    release_all_modes: list[ReleaseMode] = field(default_factory=list)
    mixed_frame_counts: list[int] = field(default_factory=list)
    stop_wait_calls: int = 0
    start_audio_calls: int = 0
    stop_audio_calls: int = 0

    def start_voice(
        self,
        voice_id: str,
        pitch: int,
        velocity: int,
        source,
        *,
        source_sample_rate: int,
        owner_tag: str,
        pan: float = 0.0,
    ) -> None:
        assert pan == 0.0
        self.started.append(
            (
                voice_id,
                pitch,
                velocity,
                source,
                source_sample_rate,
                owner_tag,
            )
        )

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self.note_offs.append((voice_id, mode))

    def release_all(self, mode: ReleaseMode) -> None:
        self.release_all_modes.append(mode)

    def mix_frames(self, frame_count: int) -> bytes:
        self.mixed_frame_counts.append(frame_count)
        return bytes(frame_count * 4)

    def release_all_and_wait_for_stop(self) -> bool:
        self.stop_wait_calls += 1
        self.release_all(ReleaseMode.STOP)
        return True

    def start_audio(self) -> None:
        self.start_audio_calls += 1

    def stop_audio(self) -> None:
        self.stop_audio_calls += 1


class RecordingToneSource:
    def __init__(self) -> None:
        self.position = 0.5
        self.voice_selection: dict[str, float] = {}
        self.note_offs: list[tuple[str, ReleaseMode]] = []
        self.release_modes: list[ReleaseMode] = []

    def start_voice(self, voice_id: str, _pitch: int, _velocity: int):
        self.voice_selection[voice_id] = self.position
        return object()

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self.note_offs.append((voice_id, mode))

    def release_all(self, mode: ReleaseMode) -> None:
        self.release_modes.append(mode)

    def snapshot_renderer(self):
        position = self.position
        owner = self

        class Snapshot:
            output_channels = 2
            selection = position

            def render_project(self, notes):
                owner.rendered_projects.append((position, tuple(notes)))
                return array("h", [1, -1])

        return Snapshot()

    rendered_projects: list[tuple[float, tuple]] = []


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class MarkerToneSource:
    def __init__(self, mixer: PianoVoiceMixer) -> None:
        self._mixer = mixer

    @property
    def mixer(self) -> PianoVoiceMixer:
        return self._mixer

    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> None:
        marker = 20_000 if pitch == 60 else -20_000
        self._mixer.start_voice(
            voice_id,
            pitch,
            velocity,
            (marker,) * 2_000,
            source_sample_rate=1_000,
            owner_tag="project",
        )

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self._mixer.note_off(voice_id, mode)


def test_real_audio_sink_pulls_from_continuous_mix_device(qtbot, monkeypatch) -> None:
    """Fails if Qt leaves a real pull sink idle without requesting mixed frames."""
    output = QMediaDevices.defaultAudioOutput()
    if output.isNull():
        pytest.skip("No usable Qt audio output device is available")

    audio_format = QAudioFormat()
    audio_format.setSampleRate(8_000)
    audio_format.setChannelCount(2)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    mixer = PianoVoiceMixer(sample_rate=8_000)
    read_requests = 0
    original_read = mixer._read_output_frames

    def count_read_requests(frame_count: int) -> bytes:
        nonlocal read_requests
        read_requests += 1
        return original_read(frame_count)

    monkeypatch.setattr(mixer, "_read_output_frames", count_read_requests)
    sink = QAudioSink(output, audio_format)
    sink.setVolume(0.0)
    sink.start(mixer.io_device)
    try:
        qtbot.waitUntil(
            lambda: read_requests > 0 and sink.processedUSecs() > 0,
            timeout=3_000,
        )
        processed_microseconds = sink.processedUSecs()
    finally:
        sink.stop()
        sink.deleteLater()

    assert read_requests > 0
    assert processed_microseconds > 0


def test_real_audio_sink_accepts_qt_audio_error_enum_without_false_failure(qapp) -> None:
    """A valid Qt 6 sink must not be rejected by enum identity checks."""
    from PySide6.QtMultimedia import QMediaDevices

    mixer = PianoVoiceMixer(sample_rate=48_000)
    output = QMediaDevices.defaultAudioOutput()
    if output.isNull():
        pytest.skip("No usable Qt audio output device is available")
    mixer.start_audio()
    try:
        assert mixer._sink is not None
    finally:
        mixer.stop_audio()


@pytest.mark.parametrize("buffer_milliseconds", [20, 40, 60, 80, 100, 200])
def test_real_audio_sink_uses_its_actual_latency_budget(
    qtbot, buffer_milliseconds: int
) -> None:
    """Transport alignment must use the buffer value actually accepted by Qt."""
    if QMediaDevices.defaultAudioOutput().isNull():
        pytest.skip("No usable Qt audio output device is available")
    mixer = PianoVoiceMixer(sample_rate=48_000)
    mixer.set_volume(0.0)
    mixer.start_audio(buffer_milliseconds=buffer_milliseconds)
    sink = mixer._sink
    assert sink is not None
    try:
        qtbot.waitUntil(lambda: sink.processedUSecs() > 0, timeout=3_000)
        actual_buffer_size = sink.bufferSize()
        assert actual_buffer_size > 0
        assert mixer.output_latency_seconds() == pytest.approx(
            actual_buffer_size / (48_000 * 4)
        )
    finally:
        mixer.stop_audio()

def _recording_sink(monkeypatch):
    class RecordingSink:
        instances: list["RecordingSink"] = []

        def __init__(self, *_args) -> None:
            self.events: list[str] = []
            self.volumes: list[float] = []
            self.device = None
            self.__class__.instances.append(self)

        def setBufferSize(self, _size: int) -> None:
            self.events.append("buffer")

        def start(self, device) -> None:
            self.events.append("start")
            self.device = device

        def reset(self) -> None:
            self.events.append("reset")

        def stop(self) -> None:
            self.events.append("stop")

        def deleteLater(self) -> None:
            self.events.append("delete")

        def suspend(self) -> None:
            self.events.append("suspend")

        def resume(self) -> None:
            self.events.append("resume")

        def setVolume(self, _volume: float) -> None:
            self.volumes.append(_volume)
            self.events.append("volume")

    monkeypatch.setattr(mixer_module, "QAudioSink", RecordingSink)
    return RecordingSink


def test_realtime_start_queues_first_note_before_the_sink_first_pull(
    qapp, monkeypatch
) -> None:
    """The first device pull must not consume an empty pre-transport buffer."""

    class EagerSink:
        latest = None

        def __init__(self, *_args) -> None:
            self.first_payload = b""
            type(self).latest = self

        def start(self, device) -> None:
            self.first_payload = device.readData(16)

        def setBufferSize(self, _size: int) -> None:
            return None

        def state(self):
            return mixer_module.QAudio.State.ActiveState

        def bufferSize(self) -> int:
            return 4_000

        def setVolume(self, _volume: float) -> None:
            return None

        def stop(self) -> None:
            return None

        def deleteLater(self) -> None:
            return None

    monkeypatch.setattr(mixer_module, "QAudioSink", EagerSink)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    source = MarkerToneSource(mixer)
    session = RealtimePlaybackSession(
        (Note.create(id="first", pitch=60, start_time=0.0, duration=10.0, velocity=127),),
        source,
        mixer,
    )

    session.start()

    assert EagerSink.latest is not None
    assert any(_samples(EagerSink.latest.first_payload))


def test_selected_loop_audition_reuses_the_sink_after_natural_completion(
    qapp, monkeypatch
) -> None:
    """Natural loop completion must not reset/recreate Qt's audio sink."""
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    source = MarkerToneSource(mixer)
    note = Note.create(id="selected", pitch=60, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((note,), frozenset({"selected"})))
    player = AudioPlayer(clock=FakeClock(), piano_source=source)
    playback = PlaybackController(editor, player)

    audition = playback.selectionAudition
    audition.setLoopSelection(True)
    audition.playSelection()
    first_session = player._temporary_session
    assert first_session is not None
    first_session._finish_voice(next(iter(first_session._active_voice_ids)))
    qapp.processEvents()

    assert len(RecordingSink.instances) == 1
    assert player._temporary_session is not None
    assert audition.playing is True


def _samples(payload: bytes) -> array:
    values = array("h")
    values.frombytes(payload)
    return values


def test_live_temporary_audition_obeys_master_volume_and_rate(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    source = MarkerToneSource(mixer)
    player = AudioPlayer(clock=FakeClock(), piano_source=source)
    player.play_temporary_notes((Note.create(id='a', pitch=60, start_time=0, duration=2),))
    player.set_volume(0.25)
    player.set_playback_rate(2.0)
    assert mixer._volume == pytest.approx(0.25)
    assert player._temporary_session.playback_rate == 2.0
    player.stop_temporary_notes()


def test_seek_after_stop_does_not_start_hidden_project_audio(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    player = AudioPlayer(clock=FakeClock(), piano_source=MarkerToneSource(mixer))
    player.play_notes((Note.create(id='a', pitch=60, start_time=0, duration=5),))
    player.stop()
    player.seek(2.0)
    assert not player.is_playing()
    assert player.position_seconds() == pytest.approx(2.0)
    player.play_notes(player._project_session_notes)
    assert player.is_playing()
    assert player.position_seconds() == pytest.approx(2.0)
    player.stop()


def test_voice_pool_disposal_after_shared_mixer_deletion_is_quiet(qapp, caplog):
    import shiboken6
    mixer = PianoVoiceMixer()
    pool = RealtimeVoicePool(mixer)
    mixer._audio_ready = True
    shiboken6.delete(mixer)
    pool.dispose()
    assert not [r for r in caplog.records if r.levelno >= 40]


def test_single_note_audition_restarts_output_after_transport_stop(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    player = AudioPlayer(clock=FakeClock(), piano_source=MarkerToneSource(mixer))
    first = player.audition(60)
    mixer.mix_frames(10)
    player.release_audition(first, ReleaseMode.STOP)
    mixer.mix_frames(100)
    player.play_notes((Note.create(id='a', pitch=60, start_time=0, duration=5),))
    mixer.mix_frames(10)
    player.stop()
    assert mixer._sink is None
    player.audition(64)
    assert mixer._sink is not None
    assert mixer.is_audio_ready()
    mixer.stop_audio()


def test_stopping_temporary_session_never_emits_natural_completion(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    player = AudioPlayer(clock=FakeClock(), piano_source=MarkerToneSource(mixer))
    finished = []
    player.temporaryFinished.connect(lambda: finished.append(True))
    player.play_temporary_notes((Note.create(id='a', pitch=60, start_time=0, duration=5),))
    mixer.mix_frames(10)
    player.stop_temporary_notes()
    qapp.processEvents()
    assert finished == []


def test_clearing_all_project_notes_stops_existing_audio(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    note = Note.create(id='a', pitch=60, start_time=0, duration=5)
    editor = EditorController(ProjectState((note,), frozenset({'a'})))
    player = AudioPlayer(clock=FakeClock(), piano_source=MarkerToneSource(mixer))
    playback = PlaybackController(editor, player)
    playback.play()
    mixer.mix_frames(10)
    editor.deleteSelected()
    editor.synchronizeNow()
    assert not player.is_playing()
    assert not playback.playing
    assert not mixer.active_voice_ids


def test_releasing_keyboard_auditions_preserves_project_voices(qapp, monkeypatch):
    _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    class Source(MarkerToneSource):
        def release_all(self, mode):
            mixer.release_all(mode)
    player = AudioPlayer(clock=FakeClock(), piano_source=Source(mixer))
    player.play_notes((Note.create(id='project', pitch=60, start_time=0, duration=5),))
    player.audition(64)
    mixer.mix_frames(20)
    player.stop_audition(ReleaseMode.STOP)
    mixer.mix_frames(100)
    assert mixer.active_voice_ids == ('project:1:project',)
    player.stop()


def test_project_stop_keeps_active_audition_deliverable(qapp, monkeypatch) -> None:
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    mixer.start_voice(
        "audition:1", 64, 127, (-20_000,) * 2_000,
        source_sample_rate=1_000, owner_tag="audition",
    )
    source = MarkerToneSource(mixer)
    session = RealtimePlaybackSession((Note.create(id="project", pitch=60, start_time=0, duration=10, velocity=127),), source, mixer)
    session.start()
    old_sink = RecordingSink.instances[0]
    old_sink.device.readData(16)

    session.stop(ReleaseMode.STOP)

    new_sink = RecordingSink.instances[1]
    continuing = _samples(new_sink.device.readData(16))
    mixer.note_off("audition:1", ReleaseMode.NATURAL)
    release_tail = _samples(new_sink.device.readData(16))
    assert old_sink.events.index("reset") < old_sink.events.index("stop")
    assert any(sample < 0 for sample in continuing)
    assert not any(sample > 0 for sample in continuing)
    assert any(sample < 0 for sample in release_tail)


def test_project_stop_before_first_note_keeps_audition_deliverable(qapp, monkeypatch) -> None:
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    mixer.start_voice(
        "audition:1", 64, 127, (-20_000,) * 2_000,
        source_sample_rate=1_000, owner_tag="audition",
    )
    source = MarkerToneSource(mixer)
    session = RealtimePlaybackSession((Note.create(id="later", pitch=60, start_time=10, duration=1, velocity=127),), source, mixer)
    session.start()
    RecordingSink.instances[0].device.readData(16)

    session.stop(ReleaseMode.STOP)

    continuing = _samples(RecordingSink.instances[1].device.readData(16))
    assert any(sample < 0 for sample in continuing)
    assert not any(sample > 0 for sample in continuing)


def test_recreated_sink_applies_current_volume_before_delivery(qapp, monkeypatch) -> None:
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    mixer.start_voice(
        "audition:1", 64, 127, (-20_000,) * 2_000,
        source_sample_rate=1_000, owner_tag="audition",
    )
    session = RealtimePlaybackSession(
        (Note.create(id="project", pitch=60, start_time=0, duration=10, velocity=127),),
        MarkerToneSource(mixer),
        mixer,
    )
    session.start()
    RecordingSink.instances[0].device.readData(16)
    mixer.set_volume(0.37)

    session.stop(ReleaseMode.STOP)

    recreated = RecordingSink.instances[1]
    assert recreated.volumes == [pytest.approx(0.37)]
    assert recreated.events.index("volume") < recreated.events.index("start")


def test_discard_reentrant_replacement_starts_deliverable_new_pcm(qapp, monkeypatch) -> None:
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    mixer.start_voice(
        "project:1:old", 60, 127, (20_000,) * 2_000,
        source_sample_rate=1_000, owner_tag="project",
    )
    mixer.start_audio(buffer_milliseconds=20)
    RecordingSink.instances[0].device.readData(16)

    def start_replacement(voice_id: str) -> None:
        if voice_id != "project:1:old":
            return
        mixer.note_off("project:1:old", ReleaseMode.STOP)
        mixer.start_voice(
            "project:2:new", 64, 127, (-20_000,) * 2_000,
            source_sample_rate=1_000, owner_tag="project",
        )
        mixer.start_audio(buffer_milliseconds=20)

    mixer.voiceFinished.connect(start_replacement)
    mixer.discard_voice_ids_and_reset_output(("project:1:old",))

    replacement = RecordingSink.instances[1]
    samples = _samples(replacement.device.readData(16))
    assert any(sample < 0 for sample in samples)
    assert not any(sample > 0 for sample in samples)
    assert not any(
        getattr(command, "voice_id", None) == "project:1:old"
        for command in mixer._commands
    )


def test_stale_pcm_never_reaches_new_project(qapp, monkeypatch) -> None:
    RecordingSink = _recording_sink(monkeypatch)
    mixer = PianoVoiceMixer(sample_rate=1_000)
    source = MarkerToneSource(mixer)
    session = RealtimePlaybackSession(
        (Note.create(id="old", pitch=60, start_time=0, duration=10, velocity=127),),
        source,
        mixer,
    )
    session.start()
    old_sink = RecordingSink.instances[0]
    old_payload = old_sink.device.readData(16)

    session.replace_notes(
        (Note.create(id="new", pitch=64, start_time=0, duration=10, velocity=127),)
    )
    new_sink = RecordingSink.instances[1]
    new_payload = new_sink.device.readData(16)

    old_samples = _samples(old_payload)
    new_samples = _samples(new_payload)
    assert any(sample > 0 for sample in old_samples)
    assert old_sink.events.index("reset") < old_sink.events.index("stop")
    assert new_sink is not old_sink
    assert any(sample < 0 for sample in new_samples)
    assert not any(sample > 0 for sample in new_samples)
    assert mixer.active_voice_ids == ("project:2:new",)


def _real_sample_source(monkeypatch):
    catalog = Path(__file__).resolve().parents[2] / "resources" / "piano" / "banks.json"
    coordinator = PianoToneCoordinator(
        catalog,
        engine_factory=lambda manifest: SamplePianoEngine(
            manifest,
            scheduler=lambda _job: None,
            preload_c3_c5_on_init=False,
        ),
    )
    selection = coordinator.commit(0.0)
    assert selection is not None
    assert selection.primary.preload_roots_sync((57,))
    mixer = PianoVoiceMixer()
    sink_buffers: list[int | None] = []
    monkeypatch.setattr(
        mixer,
        "start_audio",
        lambda *, buffer_milliseconds=None: sink_buffers.append(buffer_milliseconds),
    )
    # This helper replaces the real sink startup, so make its readiness edge
    # explicit instead of accidentally testing the PREPARING state forever.
    monkeypatch.setattr(mixer, "is_audio_ready", lambda: True)
    return ContinuousPianoToneSource(coordinator, mixer), mixer, sink_buffers


def test_tone_commit_affects_next_audition_not_current_audition(qapp) -> None:
    """Fails if AudioPlayer bypasses its injected source or mutates an active voice."""
    mixer = FakeMixer()
    source = RecordingToneSource()
    player = AudioPlayer(mixer=mixer, piano_source=source)

    first = player.audition(60, 70)
    source.position = 0.75
    second = player.audition(64, 70)
    player.release_audition(first, ReleaseMode.NATURAL)

    assert source.voice_selection[f"audition:{first}"] == 0.5
    assert source.voice_selection[f"audition:{second}"] == 0.75
    assert source.note_offs == [("audition:1", ReleaseMode.NATURAL)]
    assert mixer.start_audio_calls == 1


def test_injected_source_disposal_drains_stop_fade_before_stopping_audio(
    qapp, qtbot
) -> None:
    """Fails if source-backed audition bypasses the pool's safe shutdown lifecycle."""
    mixer = FakeMixer()
    source = RecordingToneSource()
    player = AudioPlayer(mixer=mixer, piano_source=source)
    player.audition(60, 70)

    with qtbot.waitSignal(player.destroyed, timeout=500):
        player.deleteLater()

    assert mixer.stop_wait_calls == 1
    assert mixer.stop_audio_calls == 1


def test_realtime_legacy_project_uses_continuous_source_without_project_wav(
    qapp, monkeypatch
) -> None:
    """Fails if Legacy mode bypasses the app's bounded realtime source route."""
    class LegacyCoordinator:
        @staticmethod
        def selection():
            return None

    mixer = PianoVoiceMixer()
    sink_buffers: list[int | None] = []
    monkeypatch.setattr(
        mixer,
        "start_audio",
        lambda *, buffer_milliseconds=None: sink_buffers.append(buffer_milliseconds),
    )
    legacy = LegacyPianoSource(realtime_scheduler=lambda _job: None)
    source = ContinuousPianoToneSource(LegacyCoordinator(), mixer, legacy)
    player = AudioPlayer(piano_source=source)
    monkeypatch.setattr(
        player_module,
        "_ProjectRenderJob",
        lambda *_args: pytest.fail("offline render queued"),
    )
    notes = (Note.create(id="a", pitch=60, start_time=0, duration=0.2, velocity=80),)

    player.play_notes(notes)
    pcm = array("h")
    pcm.frombytes(mixer.mix_frames(512))

    assert sink_buffers == [160]
    assert "project:1:a" in source._legacy_voices
    assert mixer._voices[0].owner_tag == "legacy-fallback"
    assert any(pcm)
    assert player._project_render_jobs == set()
    assert not tuple(player._temp_dir.glob("project-mix-*.wav"))
    player.stop()
    mixer.mix_frames(1_024)


def test_realtime_project_uses_real_sample_resources_and_keeps_natural_tail(
    qapp, monkeypatch
) -> None:
    """Fails if Sample mode falls back to Legacy or truncates release PCM."""
    source, mixer, sink_buffers = _real_sample_source(monkeypatch)
    clock = FakeClock()
    player = AudioPlayer(clock=clock, piano_source=source)
    monkeypatch.setattr(
        player_module,
        "_ProjectRenderJob",
        lambda *_args: pytest.fail("offline render queued"),
    )
    notes = (Note.create(id="sample", pitch=60, start_time=0, duration=0.01, velocity=90),)

    player.play_notes(notes)
    attack = array("h")
    attack.frombytes(mixer.mix_frames(480))
    clock.advance(0.01)
    assert player._project_session is not None
    player._project_session.process_due_events()
    tail = array("h")
    tail.frombytes(mixer.mix_frames(480))

    voice = source._voices["project:1:sample"][0]
    assert sink_buffers == [160]
    assert isinstance(voice.bank_voices[0][1], SampleVoice)
    assert mixer._voices[0].owner_tag == "continuous-tone"
    assert mixer._voices[0].envelope_mode is ReleaseMode.NATURAL
    assert any(attack)
    assert any(tail)
    assert "project:1:sample" in mixer.active_voice_ids
    assert player._project_render_jobs == set()
    assert not tuple(player._temp_dir.glob("project-mix-*.wav"))
    player.stop()
    mixer.mix_frames(1_024)


def test_temporary_session_uses_scoped_voice_ids_and_isolated_completion_signal(
    qapp, monkeypatch
) -> None:
    mixer = PianoVoiceMixer(sample_rate=1_000)
    monkeypatch.setattr(mixer, "start_audio", lambda **_kwargs: None)
    monkeypatch.setattr(mixer, "is_audio_ready", lambda: True)
    source = RecordingToneSource()
    clock = FakeClock()
    player = AudioPlayer(clock=clock, mixer=mixer, piano_source=source)
    temporary_finished: list[bool] = []
    player.temporaryFinished.connect(lambda: temporary_finished.append(True))

    player.play_notes(
        (Note.create(id="project-note", pitch=60, start_time=0.0, duration=1.0),)
    )
    player.play_temporary_notes(
        (Note.create(id="temporary-note", pitch=64, start_time=0.0, duration=1.0),)
    )

    assert "project:1:project-note" in source.voice_selection
    assert "temporary:1:temporary-note" in source.voice_selection
    assert player._project_session is not None
    assert player._temporary_session is not None
    assert player._temporary_session.is_playing is True

    player._temporary_session._finish_voice("temporary:1:temporary-note")

    # Rendering completion can lead presentation by the queued output buffer.
    # Finish only once the virtual sink has consumed the final score frame.
    assert temporary_finished == []
    monkeypatch.setattr(mixer, 'presented_audio_frame', lambda: 1000)
    player._temporary_session.process_due_events()

    assert temporary_finished == [True]
    assert player._temporary_session.is_playing is False
    assert mixer.active_voice_ids == ()


def test_duplicate_realtime_play_during_preparation_does_not_restart_session(qapp) -> None:
    class PreparingSession:
        is_preparing = True
        is_playing = False

        def __init__(self) -> None:
            self.seek_calls: list[float] = []

        def seek(self, position: float) -> None:
            self.seek_calls.append(position)

    player = AudioPlayer(mixer=FakeMixer(), piano_source=object())
    session = PreparingSession()
    notes = (Note.create(id="first", pitch=60, start_time=0.0, duration=1.0, velocity=80),)
    player._project_session = session  # type: ignore[assignment]
    player._project_session_notes = notes

    player.play_notes(notes)

    assert session.seek_calls == []


def test_audition_uses_one_mixer_and_caches_immutable_legacy_pcm(
    qapp, monkeypatch
) -> None:
    mixer = FakeMixer()
    render_calls: list[tuple[int, float, int]] = []

    def render_note(pitch: int, duration: float, velocity: int):
        render_calls.append((pitch, duration, velocity))
        return [1, -2, 3]

    monkeypatch.setattr(player_module, "render_note", render_note)
    player = AudioPlayer(mixer=mixer)

    first_voice = player.audition(60, 90)
    second_voice = player.audition(60, 90)

    assert (first_voice, second_voice) == (1, 2)
    assert render_calls == [(60, 4.0, 90)]
    assert [started[0] for started in mixer.started] == ["audition:1", "audition:2"]
    assert all(started[3] == (1, -2, 3) for started in mixer.started)
    assert all(isinstance(started[3], tuple) for started in mixer.started)
    assert all(started[4] == SAMPLE_RATE for started in mixer.started)
    assert mixer.start_audio_calls == 1


def test_natural_note_off_is_enqueued_without_stopping_the_mixer(
    qapp, monkeypatch
) -> None:
    mixer = FakeMixer()
    monkeypatch.setattr(player_module, "render_note", lambda *_args: (100,) * 8)
    player = AudioPlayer(mixer=mixer)
    voice_id = player.audition(60, 90)

    player.release_audition(voice_id, ReleaseMode.NATURAL)

    assert mixer.note_offs == [("audition:1", ReleaseMode.NATURAL)]
    assert mixer.release_all_modes == []
    assert mixer.stop_audio_calls == 0


def test_explicit_stop_audition_enqueues_stop_only_for_owned_voices(
    qapp, monkeypatch
) -> None:
    mixer = FakeMixer()
    monkeypatch.setattr(player_module, "render_note", lambda *_args: (100,) * 8)
    player = AudioPlayer(mixer=mixer)
    player.audition(60, 90)
    player.audition(64, 90)

    player.stop_audition()

    assert mixer.release_all_modes == []
    assert set(mixer.note_offs) == {('audition:1', ReleaseMode.STOP), ('audition:2', ReleaseMode.STOP)}
    assert mixer.stop_audio_calls == 0


def test_application_disposal_drains_the_twelve_ms_stop_fade_before_sink_disposal(
    qapp, qtbot, monkeypatch
) -> None:
    mixer = FakeMixer()
    monkeypatch.setattr(player_module, "render_note", lambda *_args: (100,) * 8)
    player = AudioPlayer(mixer=mixer)
    player.audition(60, 90)

    with qtbot.waitSignal(player.destroyed, timeout=500):
        player.deleteLater()

    assert mixer.release_all_modes == [ReleaseMode.STOP]
    assert mixer.stop_wait_calls == 1
    assert mixer.mixed_frame_counts == []
    assert mixer.stop_audio_calls == 1


def test_disposal_delivers_stop_pcm_through_io_device_before_stopping_sink(
    qapp, qtbot, monkeypatch
) -> None:
    mixer = PianoVoiceMixer(sample_rate=1_000)
    events: list[str] = []
    delivered: list[bytes] = []
    monkeypatch.setattr(mixer, "start_audio", lambda: None)
    monkeypatch.setattr(mixer, "stop_audio", lambda: events.append("stop"))
    pool = RealtimeVoicePool(mixer)
    pool.start(
        60,
        127,
        (12_000,) * 4_096,
        source_sample_rate=1_000,
    )
    mixer.io_device.readData(8 * 4)

    def pull_stop_tail() -> None:
        delivered.append(mixer.io_device.readData(13 * 4))
        events.append("pull")

    QTimer.singleShot(0, pull_stop_tail)

    pool.dispose()
    qtbot.waitUntil(lambda: bool(delivered), timeout=500)

    samples = array("h")
    samples.frombytes(delivered[0])
    assert any(samples[:-2])
    assert samples[-2:] == array("h", [0, 0])
    assert events == ["pull", "stop"]
