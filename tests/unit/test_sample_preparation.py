"""Memory PCM only; no audio sink or input device is opened."""
from pathlib import Path
from threading import Event, Lock

import pytest

from ai_piano_studio.audio.sample_piano import SamplePianoEngine, SampleVoice
from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.tone import PianoToneCoordinator
from ai_piano_studio.domain.note import Note
from tests.unit.test_project_session import RecordingSource, RecordingMixer

MANIFEST = Path(__file__).parents[2] / 'resources/piano/banks/layers-07-12/manifest.json'


def test_context_prefetch_never_decodes_on_caller_and_makes_cold_roots_exact():
    jobs = []
    engine = SamplePianoEngine(MANIFEST, scheduler=jobs.append, preload_c3_c5_on_init=False)
    engine.prefetch_pitches((39, 75, 81) * 1000)
    assert not engine.cached_root_midis and len(jobs) == 3
    assert not engine.ready_for_pitches((39, 75, 81))
    for job in jobs:
        job()
    assert engine.ready_for_pitches((39, 75, 81))
    for pitch in (39, 75, 81):
        voice = engine.note_on(pitch, 75)
        assert isinstance(voice, SampleVoice) and voice.root_midi == pitch
        engine.release_voice(voice)
    assert engine.cache_metrics()['misses'] == 0
    assert engine.cache_bytes <= engine.cache_metrics()['capacity_bytes']


def test_full_background_warmup_fits_budget_and_does_not_undo_inactive_generation():
    jobs = []
    engine = SamplePianoEngine(MANIFEST, scheduler=jobs.append, preload_c3_c5_on_init=False)
    engine.preload_remaining_async()
    assert len(jobs) == 18
    engine.discard_idle_cache()
    for job in jobs:
        job()
    assert engine.cache_bytes == 0
    tiny = SamplePianoEngine(MANIFEST, scheduler=jobs.append, preload_c3_c5_on_init=False, cache_byte_limit=1024)
    jobs.clear()
    tiny.preload_remaining_async()
    assert not jobs


def test_production_decoder_concurrency_is_bounded_to_two(qapp, qtbot):
    engine = SamplePianoEngine(MANIFEST, preload_c3_c5_on_init=False)
    decoder = engine._decoder
    release, lock = Event(), Lock()
    active = [0, 0]
    def slow_decode(*args, **kwargs):
        with lock:
            active[0] += 1
            active[1] = max(active)
        try:
            assert release.wait(2)
            return decoder(*args, **kwargs)
        finally:
            with lock:
                active[0] -= 1
    engine._decoder = slow_decode
    engine.preload_remaining_async()
    try:
        qtbot.waitUntil(lambda: active[0] == 2)
        assert engine.cache_metrics()['pending'] == 18
    finally:
        release.set()
    qtbot.waitUntil(lambda: engine.cache_metrics()['pending'] == 0)
    assert active[1] == 2 and engine.cache_bytes <= 24 * 1024 * 1024


class PreparingSource(RecordingSource):
    def __init__(self):
        super().__init__()
        self.ready = False
        self.requests = []

    def prefetch_pitches(self, pitches):
        self.requests.append(tuple(pitches))

    def ready_for_pitches(self, pitches):
        return self.ready


def test_session_waits_without_voice_or_device_then_starts_exactly_once(qapp, qtbot):
    source, mixer = PreparingSource(), RecordingMixer()
    session = RealtimePlaybackSession((Note.create(pitch=81, start_time=0, duration=1),), source, mixer)
    session.start()
    assert session.is_preparing and not source.started and not mixer.started_audio_with
    mixer.audioReadyChanged.emit(True)
    assert session.is_preparing and not source.started
    source.ready = True
    qtbot.waitUntil(lambda: session.is_playing)
    assert [row[1] for row in source.started] == [81]
    session.stop(ReleaseMode.STOP)


def test_device_error_after_async_preparation_is_reported_without_restart(qapp, qtbot):
    source, mixer = PreparingSource(), RecordingMixer()
    def unavailable(**kwargs):
        raise RuntimeError('controlled output unavailable')
    mixer.start_audio = unavailable
    session = RealtimePlaybackSession((Note.create(pitch=81, start_time=0, duration=1),), source, mixer)
    failures = []
    session.preparationFailed.connect(failures.append)
    session.start()
    source.ready = True
    qtbot.waitUntil(lambda: bool(failures))
    assert failures == ['controlled output unavailable']
    assert not session.is_preparing and not session.is_playing


def test_stopped_preparation_cannot_restart_and_timeout_is_bounded(qapp, qtbot, caplog):
    source, mixer = PreparingSource(), RecordingMixer()
    session = RealtimePlaybackSession((Note.create(pitch=39, start_time=0, duration=1),), source, mixer)
    session.start()
    session.stop(ReleaseMode.STOP)
    source.ready = True
    qtbot.wait(100)
    assert not source.started and not session.is_playing
    source.ready = False
    session.start()
    qtbot.waitUntil(lambda: session.is_playing, timeout=500)
    assert session.preparation_timed_out
    assert 'preparation timed out' in caplog.text
    session.stop(ReleaseMode.STOP)


def test_seek_prefetch_includes_long_sustain_older_than_the_tick_horizon(qapp):
    source, mixer = PreparingSource(), RecordingMixer()
    source.ready = True
    notes = (Note.create(pitch=39, start_time=0, duration=20),) + tuple(
        Note.create(pitch=60, start_time=i*.1, duration=.02) for i in range(1,40))
    session = RealtimePlaybackSession(notes, source, mixer)
    session.seek(4)
    assert any(39 in pitches for pitches in source.requests)
    session.stop(ReleaseMode.STOP)


def test_stopping_one_owner_does_not_open_an_output_that_was_never_started(monkeypatch):
    mixer = PianoVoiceMixer()
    def forbidden(*args):
        raise AssertionError('stop must not open an audio device')
    monkeypatch.setattr(mixer, '_start_audio_locked', forbidden)
    mixer.start_voice('audition:1', 60, 75, (1000,) * 1024,
                      source_sample_rate=48000, owner_tag='audition')
    mixer.discard_voice_ids_and_reset_output(('project:old',))
    assert mixer._sink is None
    assert any(command.voice_id == 'audition:1' for command in mixer._commands)


def test_shutdown_cancels_queued_bank_generations_before_decode():
    jobs, engines = [], []
    def factory(path):
        engine = SamplePianoEngine(path, scheduler=jobs.append, preload_c3_c5_on_init=False)
        engines.append(engine)
        return engine
    coordinator = PianoToneCoordinator(MANIFEST.parents[2]/'banks.json', engine_factory=factory)
    coordinator.commit(.25)
    assert jobs
    coordinator.cancel_pending_loads()
    for job in jobs:
        job()
    assert all(engine.cache_bytes == 0 and engine.cache_metrics()['loads'] == 0 for engine in engines)
