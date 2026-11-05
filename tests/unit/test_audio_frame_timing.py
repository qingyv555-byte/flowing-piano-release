"""B-03: score onsets measured in PCM, without a physical audio device."""
import numpy as np
import pytest

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from tests.unit.test_project_session import FakeClock, note
from ai_piano_studio.audio.profile import ReleaseMode


class PulseSource:
    def __init__(self, mixer):
        self.mixer = mixer

    def start_voice(self, voice_id, pitch, velocity):
        self.mixer.start_voice(voice_id, pitch, velocity, (16000,) * 20,
                               source_sample_rate=1000, owner_tag='project')

    def note_off(self, voice_id, mode, **kwargs):
        self.mixer.note_off(voice_id, mode, **kwargs)


@pytest.mark.parametrize('notify', [False, True])
def test_output_readiness_recovers_without_losing_future_notes(qapp, monkeypatch, notify):
    mixer = PianoVoiceMixer(sample_rate=1000)
    ready = [True]
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: ready[0])
    monkeypatch.setattr(mixer, 'processed_audio_seconds', lambda: mixer.rendered_frames / 1000)
    session = RealtimePlaybackSession(
        [note('a', 0, .04), note('b', 3, .04)], PulseSource(mixer), mixer)
    try:
        session.start()
        ready[0] = False
        session.process_due_events()
        ready[0] = True
        if notify:
            mixer.audioReadyChanged.emit(True)
        assert session._timer.isActive()
        pcm = []
        for _ in range(330):
            session.process_due_events()
            pcm.append(np.frombuffer(mixer.mix_frames(10), dtype=np.int16)[::2])
        sounding = np.concatenate(pcm) != 0
        assert np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]]).tolist() == [0, 3000]
    finally:
        session.stop(ReleaseMode.STOP)


@pytest.mark.parametrize('stall_ms', [60, 120, 300, 500])
def test_pcm_onsets_survive_gui_timer_stall(qapp, monkeypatch, stall_ms):
    clock = FakeClock()
    mixer = PianoVoiceMixer(sample_rate=1000, clock=clock)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    monkeypatch.setattr(mixer, 'processed_audio_seconds', lambda: clock.value)
    source = PulseSource(mixer)
    expected = np.arange(0, 601, 60)
    session = RealtimePlaybackSession(
        [note(str(t), t / 1000, .04) for t in expected], source, mixer, clock=clock)
    session.start()
    chunks = []
    for t in range(0, 800, 10):
        clock.value = t / 1000
        if t == 0 or t >= stall_ms:
            session.process_due_events()
        chunks.append(np.frombuffer(mixer.io_device.readData(40), dtype=np.int16)[::2])
    pcm = np.concatenate(chunks)
    sounding = pcm != 0
    actual = np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]])
    session._timer.stop()
    assert actual.tolist() == expected.tolist(), (stall_ms, actual.tolist())


@pytest.mark.parametrize('rate', [.5, 1., 2.])
@pytest.mark.parametrize('chunk', [7, 64, 257])
def test_frame_boundaries_are_independent_of_pcm_chunk_and_speed(qapp, monkeypatch, rate, chunk):
    clock = FakeClock()
    mixer = PianoVoiceMixer(sample_rate=1000, clock=clock)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    monkeypatch.setattr(mixer, 'processed_audio_seconds', lambda: mixer.rendered_frames / 1000)
    session = RealtimePlaybackSession([note(str(i), i * .15, .04) for i in range(8)], PulseSource(mixer), mixer)
    session.set_playback_rate(rate)
    session.start()
    pcm = []
    for _ in range(round(2.4 / rate * 1000 / chunk)):
        session.process_due_events()
        pcm.append(np.frombuffer(mixer.io_device.readData(chunk * 4), dtype=np.int16)[::2])
    sounding = np.concatenate(pcm) != 0
    onsets = np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]])
    assert onsets.tolist() == [round(i * 150 / rate) for i in range(8)]
    session.stop(ReleaseMode.STOP)


def test_seek_and_pause_cancel_all_old_prepared_commands(qapp, monkeypatch):
    mixer = PianoVoiceMixer(sample_rate=1000)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    monkeypatch.setattr(mixer, 'processed_audio_seconds', lambda: mixer.rendered_frames / 1000)
    finished = []
    mixer.voiceFinished.connect(finished.append)
    session = RealtimePlaybackSession([note(str(i), i * .1, .04) for i in range(9)], PulseSource(mixer), mixer)
    session.start()
    old_ids = set(session._active_voice_ids)
    session.pause()
    assert set(finished) == old_ids
    assert not mixer._timed_commands
    assert not any(np.frombuffer(mixer.mix_frames(500), dtype=np.int16))
    session.seek(.8, start_playback=False)
    session.resume()
    pcm = np.frombuffer(mixer.mix_frames(300), dtype=np.int16)[::2]
    sounding = pcm != 0
    assert np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]]).tolist() == [0]
    session.stop(ReleaseMode.STOP)


@pytest.mark.parametrize('stall_ms', [60, 120, 300, 500])
def test_output_qthread_keeps_pulling_while_gui_is_blocked(qapp, monkeypatch, stall_ms):
    from threading import Event
    from PySide6.QtCore import QObject, QTimer, QThread
    from ai_piano_studio.audio import mixer as module

    class TimerSink(QObject):
        def __init__(self, device, audio_format, parent):
            super().__init__(parent)
            self.timer = QTimer(self)
            self.timer.setInterval(5)
            self.timer.timeout.connect(self.pull)
            self.frames = 0
            self.parts = []
            self.done = Event()
            self.device = None

        def pull(self):
            assert QThread.currentThread() != qapp.thread()
            self.parts.append(self.device.readData(40))
            self.frames += 10
            if self.frames >= 800:
                self.timer.stop()
                self.done.set()

        def start(self, device):
            self.device = device
            self.timer.start()

        def state(self):
            return module.QAudio.State.ActiveState

        def processedUSecs(self):
            return self.frames * 1000

        def setBufferSize(self, size):
            self.buffer = size

        def bufferSize(self):
            return self.buffer

        def setVolume(self, volume):
            pass

        def stop(self):
            self.timer.stop()

        def reset(self):
            self.stop()

    monkeypatch.setattr(module, 'QAudioSink', TimerSink)
    mixer = PianoVoiceMixer(sample_rate=1000)
    session = RealtimePlaybackSession([note(str(i), i * .06, .04) for i in range(11)], PulseSource(mixer), mixer)
    session.start()
    sink = mixer._sink
    native = sink._sink
    notification_threads = []
    mixer.voiceFinished.connect(lambda _: notification_threads.append(QThread.currentThread()))
    try:
        # No processEvents: the main thread cannot service any QTimer here.
        Event().wait(stall_ms / 1000)
        assert native.frames > 0
        assert native.done.wait(3)
        pcm = np.frombuffer(b''.join(native.parts), dtype=np.int16)[::2]
        sounding = pcm != 0
        assert np.flatnonzero(sounding & ~np.r_[False, sounding[:-1]]).tolist() == list(range(0, 601, 60))
        qapp.processEvents()
        assert notification_threads
        assert all(thread == qapp.thread() for thread in notification_threads)
    finally:
        session.stop(ReleaseMode.STOP)
        mixer.stop_audio()


def test_natural_stop_cancels_future_notes_without_restarting_them(qapp, monkeypatch):
    mixer = PianoVoiceMixer(sample_rate=1000)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    session = RealtimePlaybackSession([note('a', 0, .04), note('b', .3, .04)], PulseSource(mixer), mixer)
    session.start()
    mixer.mix_frames(30)
    session.stop(ReleaseMode.NATURAL)
    assert not mixer._timed_commands
    assert not np.any(np.frombuffer(mixer.mix_frames(600), dtype=np.int16))


def test_presentation_clock_does_not_jump_when_buffer_budget_changes(qapp, monkeypatch):
    mixer = PianoVoiceMixer(sample_rate=1000)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    consumed = [0.]
    budget = [.16]
    monkeypatch.setattr(mixer, 'processed_audio_seconds', lambda: consumed[0])
    monkeypatch.setattr(mixer, 'output_latency_seconds', lambda: budget[0])
    session = RealtimePlaybackSession([note('a', 0, 2)], PulseSource(mixer), mixer)
    session.start()
    positions = []
    for value, latency in ((.2, .16), (.21, .04), (.19, .5), (.3, .01)):
        consumed[0], budget[0] = value, latency
        positions.append(session.position_seconds)
    assert positions == pytest.approx([.2, .21, .21, .3])
    session.stop(ReleaseMode.STOP)


@pytest.mark.parametrize('warm', [False, True])
def test_real_samples_prepare_before_pcm_and_release_lookahead_tokens(qapp, monkeypatch, warm):
    from pathlib import Path
    from ai_piano_studio.audio.sample_piano import SamplePianoEngine
    from ai_piano_studio.audio.source import ContinuousPianoToneSource
    from ai_piano_studio.audio.tone import PianoToneSelection

    jobs = []
    engine = SamplePianoEngine(Path(__file__).parents[2] / 'resources/piano/banks/layers-07-12/manifest.json',
                              scheduler=jobs.append, preload_c3_c5_on_init=False)
    selection = PianoToneSelection(0., engine, 1., None, 0., 'fixture')

    class Coordinator:
        def selection(self):
            return selection

    mixer = PianoVoiceMixer(sample_rate=48000)
    monkeypatch.setattr(mixer, 'start_audio', lambda **kw: None)
    monkeypatch.setattr(mixer, 'is_audio_ready', lambda: True)
    source = ContinuousPianoToneSource(Coordinator(), mixer)
    pitches = (39, 75, 81)
    if warm:
        engine.prefetch_pitches(pitches)
        while jobs:
            jobs.pop(0)()
    session = RealtimePlaybackSession([note(str(i), i * .15, .1, pitch) for i, pitch in enumerate(pitches)], source, mixer)
    session.start()
    if not warm:
        assert session.is_preparing and not source._voices
        while jobs:
            jobs.pop(0)()
        session._poll_sample_preparation()
    assert session.is_playing
    assert engine.cache_metrics()['misses'] == 0
    def forbidden_decode(*args, **kwargs):
        raise AssertionError('PCM consumer must not decode samples')
    monkeypatch.setattr(engine, '_decoder', forbidden_decode)
    assert np.any(np.frombuffer(mixer.mix_frames(4800), dtype=np.int16))
    session.stop(ReleaseMode.STOP)
    assert not source._voices
    assert not source._legacy_voices
    assert not mixer._timed_commands


def test_late_held_voice_advances_by_initial_seek_plus_producer_delay(qapp):
    mixer = PianoVoiceMixer(sample_rate=1000)
    mixer.mix_frames(100)
    with mixer.at_audio_frame(80):
        mixer.start_voice('late', 60, 100, (12000,) * 1000,
                          source_sample_rate=1000, owner_tag='fixture')
        mixer.seek_voice('late', .3)
    mixer.mix_frames(10)
    assert mixer._voices[0].contributions[0].read_position == pytest.approx(330)
