from __future__ import annotations

from dataclasses import dataclass
import logging

import pytest

from PySide6.QtCore import QObject, Signal

from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.domain.note import Note


@dataclass
class FakeClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RecordingSource:
    def __init__(self) -> None:
        self.started: list[tuple[str, int, int]] = []
        self.released: list[tuple[str, ReleaseMode]] = []

    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> None:
        self.started.append((voice_id, pitch, velocity))

    def note_off(self, voice_id: str, mode: ReleaseMode) -> None:
        self.released.append((voice_id, mode))


class ReleaseRecordingSource(RecordingSource):
    def __init__(self) -> None:
        super().__init__()
        self.release_details: list[tuple[str, ReleaseMode, float | None]] = []

    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode,
        *,
        release_seconds: float | None = None,
    ) -> None:
        self.release_details.append((voice_id, mode, release_seconds))
        super().note_off(voice_id, mode)


class FailingRecordingSource(RecordingSource):
    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> None:
        if pitch == 20:
            raise ValueError("sample range does not include pitch 20")
        super().start_voice(voice_id, pitch, velocity)


class RecordingMixer(QObject):
    voiceFinished = Signal(str)
    audioReadyChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.started_audio_with: list[int] = []
        self.pulls: list[int] = []
        self.pause_calls = 0
        self.resume_calls = 0
        self.volumes: list[float] = []
        self.sink_active = False
        self.sink_suspended = False
        self.discarded_voice_ids: list[tuple[str, ...]] = []
        self.processed_seconds: float | None = None
        self.output_latency = 0.0
        self.audio_ready = True

    def start_audio(self, *, buffer_milliseconds: int) -> None:
        self.started_audio_with.append(buffer_milliseconds)
        if not self.sink_active:
            self.sink_active = True
            self.sink_suspended = False

    def is_audio_ready(self) -> bool:
        return self.audio_ready

    def pull(self, frame_count: int) -> None:
        self.pulls.append(frame_count)

    def pause_audio(self) -> None:
        self.pause_calls += 1
        self.sink_suspended = True

    def resume_audio(self) -> None:
        self.resume_calls += 1
        self.sink_suspended = False

    def set_volume(self, volume: float) -> None:
        self.volumes.append(volume)

    def processed_audio_seconds(self) -> float | None:
        return self.processed_seconds

    def output_latency_seconds(self) -> float:
        return self.output_latency

    def discard_voice_ids_and_reset_output(self, voice_ids: tuple[str, ...]) -> None:
        self.discarded_voice_ids.append(tuple(sorted(voice_ids)))
        self.sink_active = False
        self.sink_suspended = False

    def emit_voice_finished(self, voice_id: str) -> None:
        self.voiceFinished.emit(voice_id)


class DelayedReadyMixer(RecordingMixer):
    def __init__(self) -> None:
        super().__init__()
        self.audio_ready = False

    def processed_audio_seconds(self) -> float | None:
        return self.processed_seconds if self.audio_ready else None

    def mark_audio_ready(self) -> None:
        self.audio_ready = True
        self.processed_seconds = 0.0
        self.audioReadyChanged.emit(True)


def test_failed_output_start_retires_primed_voices(qapp):
    class FailingMixer(RecordingMixer):
        def start_audio(self, **kwargs):
            raise RuntimeError('output unavailable')
    source, mixer = RecordingSource(), FailingMixer()
    session = RealtimePlaybackSession((note('a', 0),), source, mixer)
    with pytest.raises(RuntimeError, match='output unavailable'):
        session.start()
    assert not session._active_voice_ids
    assert source.released == [('project:1:a', ReleaseMode.STOP)]


def note(
    identifier: str, start_time: float, duration: float = 1.0, pitch: int = 60
) -> Note:
    return Note.create(
        id=identifier,
        pitch=pitch,
        start_time=start_time,
        duration=duration,
        velocity=90,
    )


def test_realtime_playback_starts_first_voice_without_full_project_render(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("first", 0.0), note("later", 30.0)), source, mixer, clock=clock)
    session.start()
    assert source.started == [("project:1:first", 60, 90)]
    assert mixer.started_audio_with == [160]


def test_cold_start_holds_transport_and_prevents_an_overdue_note_burst(qapp):
    source, mixer, clock = RecordingSource(), DelayedReadyMixer(), FakeClock()
    notes = tuple(note(str(index), index * 0.1, duration=1.0, pitch=60 + index) for index in range(4))
    session = RealtimePlaybackSession(notes, source, mixer, clock=clock)
    states: list[str] = []
    session.stateChanged.connect(states.append)

    session.start()
    assert session.is_playing is False
    assert session.is_preparing is True
    assert states == ["preparing"]
    assert source.started == [("project:1:0", 60, 90)]
    assert session.position_seconds == pytest.approx(0.0)

    clock.advance(0.25)
    session.process_due_events()

    assert source.started == [("project:1:0", 60, 90)]
    assert session.position_seconds == pytest.approx(0.0)

    mixer.mark_audio_ready()
    assert session.is_playing is True
    assert session.position_seconds == pytest.approx(0.0)
    assert states == ["preparing", "playing"]

    clock.advance(0.1)
    mixer.processed_seconds = 0.1
    session.process_due_events()

    assert [voice_id for voice_id, _pitch, _velocity in source.started] == [
        "project:1:0",
        "project:1:1",
    ]


def test_cold_start_scheduler_uses_audio_clock_after_wall_clock_stalls(qapp):
    source, mixer, clock = RecordingSource(), DelayedReadyMixer(), FakeClock()
    notes = tuple(note(str(index), index * 0.1, duration=1.0, pitch=60 + index) for index in range(4))
    session = RealtimePlaybackSession(notes, source, mixer, clock=clock)

    session.start()
    clock.advance(1.0)
    session.process_due_events()
    assert source.started == [("project:1:0", 60, 90)]

    mixer.mark_audio_ready()
    clock.advance(1.0)
    session.process_due_events()
    assert source.started == [("project:1:0", 60, 90)]

    mixer.processed_seconds = 0.11
    session.process_due_events()
    mixer.processed_seconds = 0.21
    session.process_due_events()
    mixer.processed_seconds = 0.31
    session.process_due_events()
    assert [voice_id for voice_id, _pitch, _velocity in source.started] == [
        "project:1:0",
        "project:1:1",
        "project:1:2",
        "project:1:3",
    ]


def test_seek_inside_sustained_note_restores_it_at_the_audio_epoch(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession(
        (note("sustain", 0.0, duration=5.0), note("later", 3.0, pitch=64)),
        source,
        mixer,
        clock=clock,
    )


    session.seek(2.0)

    assert session.is_playing is True
    assert session.position_seconds == pytest.approx(2.0)
    assert source.started == [("project:2:sustain", 60, 90)]


def test_release_metadata_maps_to_exact_voice_note_off_modes(qapp) -> None:
    source, mixer, clock = ReleaseRecordingSource(), RecordingMixer(), FakeClock()
    notes = (
        Note.create(
            id="natural",
            pitch=60,
            start_time=0.0,
            duration=0.1,
            velocity=90,
        ),
        Note.create(
            id="custom",
            pitch=60,
            start_time=0.0,
            duration=0.1,
            velocity=90,
            release_mode="custom",
            release_ms=420.0,
        ),
        Note.create(
            id="cut",
            pitch=60,
            start_time=0.0,
            duration=0.1,
            velocity=90,
            release_mode="cut",
        ),
    )
    session = RealtimePlaybackSession(notes, source, mixer, clock=clock)

    session.start()
    clock.advance(0.2)
    session.process_due_events()

    assert source.release_details == [
        ("project:1:custom", ReleaseMode.NATURAL, 0.42),
        ("project:1:cut", ReleaseMode.STOP, None),
        ("project:1:natural", ReleaseMode.NATURAL, None),
    ]


def test_paused_seek_restores_sustained_note_before_resume(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession(
        (note("sustain", 0.0, duration=5.0), note("later", 3.0, pitch=64)),
        source,
        mixer,
        clock=clock,
    )

    session.start()
    session.pause()
    session.seek(2.0)
    assert source.started == [("project:1:sustain", 60, 90)]

    session.resume()

    assert source.started == [
        ("project:1:sustain", 60, 90),
        ("project:2:sustain", 60, 90),
    ]


def test_nonzero_seek_holds_transport_until_audio_is_ready(qapp):
    source, mixer, clock = RecordingSource(), DelayedReadyMixer(), FakeClock()
    session = RealtimePlaybackSession(
        (note("sustain", 0.0, duration=5.0), note("later", 3.0, pitch=64)),
        source,
        mixer,
        clock=clock,
    )

    session.seek(2.0)
    clock.advance(1.0)
    session.process_due_events()

    assert session.state.value == "preparing"
    assert session.position_seconds == pytest.approx(2.0)
    assert source.started == [("project:2:sustain", 60, 90)]

    mixer.mark_audio_ready()
    assert session.state.value == "playing"
    assert session.position_seconds == pytest.approx(2.0)


def test_realtime_playback_skips_a_failed_voice_without_crashing_transport(
    qapp, caplog
) -> None:
    source, mixer, clock = FailingRecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession(
        (note("bad", 0.0, pitch=20), note("good", 0.1)),
        source,
        mixer,
        clock=clock,
    )

    with caplog.at_level(logging.ERROR, logger="ai_piano_studio.audio.project_session"):
        session.start()

    assert session.is_playing
    assert source.started == []
    assert "Skipping playback note" in caplog.text

    clock.advance(0.2)
    session.process_due_events()

    assert source.started == [("project:1:good", 60, 90)]


def test_realtime_playback_uses_multiple_bounded_sink_pulls(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("first", 0.0, 0.1), note("second", 0.2, 0.1)), source, mixer, clock=clock)
    session.start(); mixer.pull(4800)
    clock.advance(0.21); session.process_due_events(); mixer.pull(4800)
    assert [item[0] for item in source.started] == ["project:1:first", "project:1:second"]
    assert source.released == [("project:1:first", ReleaseMode.NATURAL)]


def test_note_audio_is_aligned_with_transport_clock(qapp) -> None:
    """The visible transport must not advance beyond output consumption."""
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("c4", 2.0),), source, mixer, clock=clock)

    mixer.processed_seconds = 0.0
    mixer.output_latency = 0.2
    session.start()
    clock.advance(2.2)
    mixer.processed_seconds = 2.2
    session.process_due_events()

    assert source.started == [("project:1:c4", 60, 90)]
    assert session.position_seconds == pytest.approx(2.0)


@pytest.mark.parametrize('rate,elapsed,expected', [(2.0, 1.1, True), (0.5, 2.1, False)])
def test_sink_clock_scales_note_on_schedule_with_playback_rate(qapp, rate, elapsed, expected):
    source, mixer = RecordingSource(), RecordingMixer()
    mixer.processed_seconds = 0.0
    session = RealtimePlaybackSession((note('later', 2.0),), source, mixer)
    session.set_playback_rate(rate)
    session.start()
    mixer.processed_seconds = elapsed
    session.process_due_events()
    assert bool(source.started) is expected


def test_pause_and_resume_do_not_jump_ahead_of_sink_consumption(qapp) -> None:
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("c4", 10.0),), source, mixer, clock=clock)

    mixer.processed_seconds = 0.0
    mixer.output_latency = 0.2
    session.start()
    clock.advance(2.0)
    mixer.processed_seconds = 1.2
    session.pause()
    session.resume()

    assert session.position_seconds == pytest.approx(1.0)


def test_playback_rate_change_preserves_the_sink_aligned_position(qapp) -> None:
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("c4", 10.0),), source, mixer, clock=clock)

    mixer.processed_seconds = 0.0
    mixer.output_latency = 0.2
    session.start()
    clock.advance(2.2)
    mixer.processed_seconds = 2.2
    assert session.position_seconds == pytest.approx(2.0)

    session.set_playback_rate(2.0)

    assert session.position_seconds == pytest.approx(2.0)


def test_reducing_sink_buffer_does_not_change_note_timing(qapp) -> None:
    started_voice_ids: list[list[str]] = []
    for buffer_milliseconds in (80, 200):
        source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
        session = RealtimePlaybackSession(
            (note("c4", 2.0),),
            source,
            mixer,
            clock=clock,
            buffer_milliseconds=buffer_milliseconds,
        )
        session.start()
        clock.advance(2.0)
        session.process_due_events()
        started_voice_ids.append([voice_id for voice_id, _pitch, _velocity in source.started])

    assert started_voice_ids == [["project:1:c4"], ["project:1:c4"]]


def started_session(*notes: Note) -> tuple[RealtimePlaybackSession, RecordingSource, RecordingMixer, FakeClock]:
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession(notes, source, mixer, clock=clock)
    session.start()
    return session, source, mixer, clock


def test_stop_flushes_pending_pcm(qapp):
    session, source, mixer, _clock = started_session(note("first", 0.0, 10.0))
    session.stop(ReleaseMode.STOP)
    session.process_due_events()
    assert source.released == [("project:1:first", ReleaseMode.STOP)]
    assert mixer.discarded_voice_ids == [("project:1:first",)]
    assert session.is_playing is False


def test_stop_then_play_clears_reset_before_pause_resume(qapp):
    session, _source, mixer, _clock = started_session(note("first", 0.0, 10.0))

    session.stop(ReleaseMode.STOP)
    session.start()
    session.pause()
    session.resume()

    assert mixer.sink_suspended is False
    assert mixer.resume_calls == 1


def test_seek_invalidates_old_stream_generation(qapp):
    session, source, _mixer, clock = started_session(
        note("first", 0.0, 1.0), note("second", 1.0, 1.0, 64)
    )
    session.seek(1.0)
    clock.advance(0.02)
    session.process_due_events()
    assert ("project:1:first", ReleaseMode.STOP) in source.released
    assert source.started[-1] == ("project:2:second", 64, 90)


def test_realtime_sample_session_requests_natural_release_tail(qapp):
    session, source, mixer, clock = started_session(note("tail", 0.0, 0.1))
    clock.advance(0.1)
    session.process_due_events()
    mixer.emit_voice_finished("project:1:tail")
    assert source.released == [("project:1:tail", ReleaseMode.NATURAL)]
    assert mixer.discarded_voice_ids == []


def test_release_tail_does_not_extend_reported_musical_position(qapp):
    session, source, _mixer, clock = started_session(note("tail", 0.0, 0.1))

    clock.advance(1.0)
    session.process_due_events()

    assert source.released == [("project:1:tail", ReleaseMode.NATURAL)]
    assert session.position_seconds == pytest.approx(0.1)
    assert session.is_playing is True


def test_pause_and_resume_delegate_to_the_mixer_sink(qapp):
    session, _source, mixer, _clock = started_session(note("first", 0.0, 1.0))
    session.pause()
    session.resume()
    assert (mixer.pause_calls, mixer.resume_calls) == (1, 1)


def test_seek_starts_audio_for_a_fresh_session(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("first", 0.0),), source, mixer, clock=clock)
    session.seek(0.0)
    assert mixer.started_audio_with == [160]


def test_pause_then_seek_stays_paused(qapp):
    session, source, mixer, _clock = started_session(
        note("first", 0.0, 10.0), note("second", 1.0, 10.0, 64)
    )
    session.pause()

    session.seek(1.0)

    assert session.position_seconds == pytest.approx(1.0)
    assert mixer.sink_suspended is False
    assert mixer.resume_calls == 0
    assert mixer.started_audio_with == [160]
    assert source.started == [("project:1:first", 60, 90)]
    assert mixer.discarded_voice_ids == [("project:1:first",)]

    session.resume()

    assert mixer.started_audio_with == [160, 160]
    assert source.started[-1] == ("project:2:second", 64, 90)


def test_realtime_session_uses_a_ui_stall_resistant_sink_budget(qapp) -> None:
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("first", 0.0),), source, mixer, clock=clock)

    session.start()

    assert mixer.started_audio_with == [160]


def test_repeated_paused_seek_stays_paused_until_resume(qapp):
    session, source, mixer, _clock = started_session(
        note("first", 0.0, 10.0),
        note("second", 1.0, 10.0, 64),
        note("third", 2.0, 10.0, 67),
    )
    session.pause()

    session.seek(1.0)
    session.seek(2.0)

    assert session.position_seconds == pytest.approx(2.0)
    assert mixer.started_audio_with == [160]
    assert mixer.resume_calls == 0
    assert source.started == [("project:1:first", 60, 90)]
    assert mixer.discarded_voice_ids == [("project:1:first",), ()]

    session.resume()

    assert source.started[-1] == ("project:3:third", 67, 90)


@pytest.mark.parametrize("mode", [ReleaseMode.SAFE, ReleaseMode.NATURAL])
def test_safe_and_natural_stop_keep_project_release_tail(qapp, mode):
    session, source, mixer, _clock = started_session(note("first", 0.0, 10.0))

    session.stop(mode)

    assert source.released == [("project:1:first", mode)]
    assert mixer.discarded_voice_ids == []
    assert session.is_playing is False


def test_replacing_notes_after_pause_resumes_the_existing_sink(qapp):
    session, source, mixer, _clock = started_session(note("old", 0.0, 10.0))
    session.pause()

    session.replace_notes((note("new", 0.0),))

    assert mixer.sink_suspended is False
    assert mixer.resume_calls == 1
    assert source.started[-1] == ("project:2:new", 60, 90)


@pytest.mark.parametrize("seconds", [1.0, 99.0])
def test_seek_at_or_beyond_project_end_finishes_without_staying_active(qapp, seconds):
    session, _source, mixer, _clock = started_session(note("only", 0.0, 1.0))

    session.seek(seconds)

    assert session.position_seconds == pytest.approx(1.0)
    assert session.is_playing is False
    assert mixer.sink_suspended is False


def test_replace_notes_invalidates_old_generation_and_starts_new_snapshot(qapp):
    session, source, mixer, _clock = started_session(note("old", 0.0, 10.0))

    session.replace_notes((note("new", 0.0),))

    assert ("project:1:old", ReleaseMode.STOP) in source.released
    assert source.started[-1] == ("project:2:new", 60, 90)
    assert mixer.started_audio_with == [160, 160]


def test_playback_rate_scales_position_and_due_note_scheduling(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("later", 1.0),), source, mixer, clock=clock)
    session.start()

    session.set_playback_rate(2.0)
    clock.advance(0.5)
    session.process_due_events()

    assert session.position_seconds == pytest.approx(1.0)
    assert source.started == [("project:1:later", 60, 90)]


def test_playback_rate_change_preserves_elapsed_logical_position(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("later", 10.0),), source, mixer, clock=clock)
    session.start()
    clock.advance(1.0)

    session.set_playback_rate(2.0)
    clock.advance(0.5)

    assert session.position_seconds == pytest.approx(2.0)


def test_volume_is_applied_when_the_realtime_sink_starts(qapp):
    source, mixer, clock = RecordingSource(), RecordingMixer(), FakeClock()
    session = RealtimePlaybackSession((note("first", 0.0),), source, mixer, clock=clock)

    session.set_volume(0.35)
    session.start()

    assert mixer.volumes == [0.35]


@pytest.mark.parametrize("rate", [0.0, -1.0, float("inf"), float("nan"), True])
def test_playback_rate_rejects_non_positive_or_non_finite_values(qapp, rate):
    session = RealtimePlaybackSession(
        (note("first", 0.0),), RecordingSource(), RecordingMixer()
    )

    with pytest.raises(ValueError, match="positive finite"):
        session.set_playback_rate(rate)
