from concurrent.futures import Future

import numpy as np

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.native_playback import NativePlaybackSession, NativeScoreStream
from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.sfizz_renderer import _events_for_score
from ai_piano_studio.domain.control_event import ControlEvent
from ai_piano_studio.domain.note import Note


class RecordingSynth:
    def __init__(self):
        self.events = []
        self.frames = 0
        self.blocks = []

    def send(self, event, offset):
        self.events.append((self.frames + offset, event.kind))

    def block(self, frames):
        self.blocks.append(frames)
        self.frames += frames
        return np.tile(np.array([1300, -600], dtype='<i2'), frames)


def test_native_blocks_keep_frame_zero_and_pedal_order_across_short_pulls():
    note = Note.create(pitch=60, start_time=0, duration=.04, velocity=90)
    controls = (ControlEvent.create(time=0, controller=64, value=127),
                ControlEvent.create(time=.04, controller=64, value=0))
    events, total = _events_for_score((note,), controls=controls, sample_rate=48000, playback_rate=2)
    synth = RecordingSynth()
    stream = NativeScoreStream(synth, events, total)
    assert stream.pull(1).tolist() == [[1300, -600]]
    stream.pull(511)
    stream.pull(531)
    assert synth.events == [(0, 'cc'), (0, 'on'), (960, 'cc'), (960, 'off')]
    assert max(synth.blocks) == 512 and stream.cursor == 1043


def test_mixer_stop_fades_last_native_sample_without_pulling_future_notes(qapp):
    mixer = PianoVoiceMixer()
    synth = RecordingSynth()
    notes = (Note.create(pitch=60, start_time=0, duration=.3),
             Note.create(pitch=61, start_time=.02, duration=.3))
    events, total = _events_for_score(notes, sample_rate=mixer.sample_rate)
    stream = NativeScoreStream(synth, events, total)
    assert mixer.attach_native_stream(stream) == 0
    initial = np.frombuffer(mixer.mix_frames(500), dtype='<i2').reshape(-1, 2)
    mixer.detach_native_stream(stream, fade=True)
    tail = np.frombuffer(mixer.mix_frames(2000), dtype='<i2').reshape(-1, 2)
    assert np.array_equal(initial[-1], tail[0])
    assert not np.any(tail[-1])
    assert synth.events == [(0, 'on')]


class PendingEngine:
    def __init__(self):
        self.jobs = []
        self.abandoned = []

    def prepare(self, notes, position, rate, cancelled):
        job = Future()
        self.jobs.append((position, rate, cancelled, job))
        return job

    def abandon(self, job):
        self.abandoned.append(job)


def test_seek_and_stop_invalidate_inflight_native_preparation(qapp):
    engine = PendingEngine()
    notes = (Note.create(pitch=60, start_time=0, duration=2),)
    session = NativePlaybackSession(notes, engine, PianoVoiceMixer())
    session.start()
    session.seek(.7)
    session.set_playback_rate(2)
    assert [(position,rate) for position,rate,_,_ in engine.jobs] == [(0,1),(.7,1),(.7,2)]
    assert all(row[2].is_set() for row in engine.jobs[:2])
    session.stop(ReleaseMode.STOP)
    assert not session.is_playing and not session.is_preparing
    assert all(row[2].is_set() for row in engine.jobs)
    assert engine.abandoned == [row[3] for row in engine.jobs]


def test_switching_back_starts_at_seek_without_a_full_gain_held_attack(qapp, monkeypatch):
    mixer = PianoVoiceMixer()
    monkeypatch.setattr(mixer, 'start_audio', lambda **_: mixer._set_audio_ready_locked(True))
    monkeypatch.setattr(mixer, 'presented_audio_frame', lambda: 0)

    class Source:
        def __init__(self):
            self.pitches = []

        def start_voice(self, voice, pitch, velocity):
            self.pitches.append(pitch)
            mixer.start_voice(voice, pitch, velocity, [6000]*48000,
                              source_sample_rate=48000, owner_tag='project', looping=True)

        def note_off(self, voice, mode):
            mixer.note_off(voice, mode)

    source = Source()
    notes = (Note.create(pitch=55, start_time=0, duration=.1),
             Note.create(pitch=60, start_time=1, duration=1))
    session = RealtimePlaybackSession(notes, source, mixer, initial_position=1.5)
    try:
        session.start()
        pcm = np.frombuffer(mixer.mix_frames(512),dtype='<i2').reshape(-1,2)
        assert source.pitches == [60]
        assert abs(int(pcm[0,0])) < 200
        assert np.abs(pcm).max() > 3000
    finally:
        session.stop(ReleaseMode.STOP)
