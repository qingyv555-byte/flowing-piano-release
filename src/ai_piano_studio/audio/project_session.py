from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable
from enum import Enum
import logging
from math import isfinite
from time import monotonic, perf_counter
from heapq import heappush, heappop

from PySide6.QtCore import QObject, QTimer, Signal

from ai_piano_studio.audio.profile import ReleaseMode
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.control_event import sounding_duration


logger = logging.getLogger(__name__)


class PlaybackState(str, Enum):
    STOPPED = "stopped"
    PREPARING = "preparing"
    PLAYING = "playing"
    PAUSED = "paused"


class RealtimePlaybackSession(QObject):
    """Schedule one project snapshot into the realtime piano voice path."""

    stateChanged = Signal(str)
    positionChanged = Signal(float)
    playingChanged = Signal(bool)
    finished = Signal()
    preparationFailed = Signal(str)

    def __init__(
        self,
        notes: Iterable[Note],
        source: object,
        mixer: object,
        *,
        clock: Callable[[], float] = monotonic,
        buffer_milliseconds: int = 160,
        voice_prefix: str = "project",
        initial_position: float = 0.0,
    ) -> None:
        super().__init__()
        if not isinstance(voice_prefix, str) or not voice_prefix.strip():
            raise ValueError("voice_prefix must be a non-empty string")
        self._notes = _note_snapshot(notes)
        self._starts = tuple((note.start_time, note.id) for note in self._notes)
        self._project_end_seconds = max(
            (note.start_time + sounding_duration(note) for note in self._notes), default=0.0
        )
        self._source = source
        self._mixer = mixer
        self._clock = clock
        self._buffer_milliseconds = buffer_milliseconds
        self._voice_prefix = voice_prefix.strip().rstrip(":")
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self.process_due_events)
        self._preparation_timer = QTimer(self)
        self._preparation_timer.setInterval(2)
        self._preparation_timer.timeout.connect(self._poll_sample_preparation)
        self._preparation_deadline = 0.0
        self._preparation_resume = False
        self.preparation_wait_ms = 0.0
        self.preparation_timed_out = False
        self._last_prefetch = float('-inf')
        self._generation = 1
        self._state = PlaybackState.STOPPED
        self._position = min(self._project_end_seconds, max(0.0, initial_position))
        self._render_position = self._position
        self._fade_initial_seek = self._position > 0.0
        self._started_at = 0.0
        self._audio_clock_origin: float | None = None
        self._next_note_index = 0
        self._active_voice_ids: set[str] = set()
        self._pending_releases = []
        self._released_voice_ids: set[str] = set()
        self._is_playing = False
        self._is_paused = False
        self._output_reset = False
        self._needs_prime_on_resume = False
        self._playback_rate = 1.0
        self._volume = 1.0
        self._frame_timing = callable(getattr(mixer, 'at_audio_frame', None))
        self._frame_origin = 0
        self._last_presentation = 0.0
        self._completion_frame = 0
        self._mixer.voiceFinished.connect(self._finish_voice)  # type: ignore[attr-defined]
        destroyed = getattr(self._mixer, 'destroyed', None)
        if destroyed is not None:
            destroyed.connect(self._timer.stop)
            destroyed.connect(self._preparation_timer.stop)
        audio_ready_signal = getattr(self._mixer, "audioReadyChanged", None)
        if audio_ready_signal is not None and callable(
            getattr(audio_ready_signal, "connect", None)
        ):
            audio_ready_signal.connect(self._on_audio_ready_changed)

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_preparing(self) -> bool:
        return self._state is PlaybackState.PREPARING

    @property
    def is_playing(self) -> bool:
        return self._state is PlaybackState.PLAYING

    @property
    def position_seconds(self) -> float:
        if self._state is not PlaybackState.PLAYING:
            return self._position
        consumed_seconds = self._audio_elapsed_seconds()
        if consumed_seconds is None:
            return self._render_position_seconds()
        output_latency = 0.0 if self._frame_timing else self._mixer_output_latency_seconds()
        audible_position = (
            self._position
            + consumed_seconds * self._playback_rate
            - (output_latency or 0.0) * self._playback_rate
        )
        result = min(
            self._project_end_seconds,
            max(self._position, audible_position),
        )
        if self._frame_timing:
            self._last_presentation = max(self._last_presentation, result)
            return self._last_presentation
        return result

    @property
    def playback_rate(self) -> float:
        return self._playback_rate

    def start(self) -> None:
        if self._state is PlaybackState.PLAYING:
            return
        if self._state is PlaybackState.PREPARING:
            return
        self._is_paused = False
        self._needs_prime_on_resume = False
        self._prepare_samples()

    def pause(self) -> None:
        if self._state is PlaybackState.PREPARING:
            self._stop(ReleaseMode.STOP, preserve_pause=True)
            return
        if self._state is not PlaybackState.PLAYING:
            return
        self._position = self.position_seconds
        self._render_position = self._position
        if self._frame_timing:
            self._stop(ReleaseMode.STOP, preserve_pause=True)
            return
        self._is_paused = True
        self._audio_clock_origin = None
        self._timer.stop()
        self._mixer.pause_audio()  # type: ignore[attr-defined]
        self._set_state(PlaybackState.PAUSED)

    def resume(self) -> None:
        if self._state is PlaybackState.PLAYING:
            return
        if self._state is not PlaybackState.PAUSED:
            return
        if self._needs_prime_on_resume:
            self._needs_prime_on_resume = False
            self._prepare_samples(resume_existing=True)
            return
        self._is_paused = False
        self._set_state(PlaybackState.PREPARING)
        if self._output_reset:
            self._start_audio()
            self._output_reset = False
        else:
            self._mixer.resume_audio()  # type: ignore[attr-defined]
        if self._mixer_is_audio_ready():
            self._enter_playing()

    def replace_notes(self, notes: Iterable[Note]) -> None:
        replacement = _note_snapshot(notes)
        self.stop(ReleaseMode.STOP)
        self._notes = replacement
        self._starts = tuple((note.start_time, note.id) for note in self._notes)
        self._project_end_seconds = max(
            (note.start_time + sounding_duration(note) for note in self._notes), default=0.0
        )
        self._position = 0.0
        self._render_position = 0.0
        self._next_note_index = 0
        self._needs_prime_on_resume = False
        self._prepare_samples(resume_existing=True)

    def set_playback_rate(self, rate: float) -> None:
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not isfinite(rate)
            or rate <= 0.0
        ):
            raise ValueError("playback rate must be a positive finite number")
        if self._frame_timing and self._is_playing:
            position = self.position_seconds
            self._playback_rate = float(rate)
            self.seek(position)
            return
        if self._is_playing:
            presentation_position = self.position_seconds
            self._render_position = presentation_position
            self._position = presentation_position
            self._started_at = self._clock()
            self._reset_audio_clock_origin()
        self._playback_rate = float(rate)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._is_playing:
            self._mixer.set_volume(self._volume)  # type: ignore[attr-defined]

    def stop(self, mode: ReleaseMode) -> None:
        self._stop(mode, preserve_pause=False)

    def _stop(self, mode: ReleaseMode, *, preserve_pause: bool) -> None:
        self._generation += 1
        self._timer.stop()
        self._preparation_timer.stop()
        self._audio_clock_origin = None
        self._pending_releases.clear()
        voice_ids = tuple(self._active_voice_ids)
        # Retire transport ownership before mixer callbacks can run. A STOP
        # discards voices synchronously; that is not natural completion.
        if mode is ReleaseMode.STOP:
            self._active_voice_ids.clear()
            self._released_voice_ids.clear()
        elif self._frame_timing:
            self._mixer.cancel_timed_voice_ids(voice_ids)
        for voice_id in voice_ids:
            self._source.note_off(voice_id, mode)  # type: ignore[attr-defined]
        if mode is ReleaseMode.STOP:
            self._mixer.discard_voice_ids_and_reset_output(voice_ids)  # type: ignore[attr-defined]
            self._output_reset = True
        self._is_paused = preserve_pause
        self._needs_prime_on_resume = preserve_pause
        self._set_state(
            PlaybackState.PAUSED if preserve_pause else PlaybackState.STOPPED
        )

    def seek(self, seconds: float, *, start_playback: bool = True) -> None:
        was_paused = self._is_paused or not start_playback
        self._stop(ReleaseMode.STOP, preserve_pause=was_paused)
        self._position = min(self._project_end_seconds, max(0.0, float(seconds)))
        self._render_position = self._position
        self._last_presentation = self._position
        self.positionChanged.emit(self._position)
        if was_paused:
            return
        if not self._has_playable_note_at_position():
            self._next_note_index = bisect_left(
                [(note.start_time, note.id) for note in self._notes],
                (self._position, ""),
            )
            return
        self._prepare_samples()

    def process_due_events(self) -> None:
        if self._state is not PlaybackState.PLAYING:
            return
        if self._frame_timing:
            if not self._mixer_is_audio_ready():
                # Keep polling while PLAYING: readiness can recover without
                # another preparation transition or even a signal edge.
                return
            self._plan_audio_frames()
            self.positionChanged.emit(self.position_seconds)
            self._finish_if_presented()
            return
        schedule_position = self._scheduling_position_seconds()
        if schedule_position < self._last_prefetch or schedule_position - self._last_prefetch >= .25:
            self._prefetch_source(schedule_position)
            self._last_prefetch = schedule_position
        presentation_position = self.position_seconds
        while (
            self._next_note_index < len(self._notes)
            and self._notes[self._next_note_index].start_time <= schedule_position
        ):
            note = self._notes[self._next_note_index]
            self._queue_note(note)
            self._next_note_index += 1
        while self._pending_releases and self._pending_releases[0][0] <= presentation_position:
            _, voice_id, note = heappop(self._pending_releases)
            if voice_id in self._active_voice_ids:
                self._release_note(note, voice_id)
                self._released_voice_ids.add(voice_id)
        self.positionChanged.emit(self.position_seconds)

    def _render_position_seconds(self) -> float:
        return min(
            self._project_end_seconds,
            self._render_position
            + (self._clock() - self._started_at) * self._playback_rate,
        )

    def _scheduling_position_seconds(self) -> float:
        audio_elapsed = self._audio_elapsed_seconds()
        if audio_elapsed is None:
            return self._render_position_seconds()
        return min(
            self._project_end_seconds,
            self._position
            + (audio_elapsed + (self._mixer_output_latency_seconds() or 0.0))
            * self._playback_rate,
        )

    def _audio_elapsed_seconds(self) -> float | None:
        if self._frame_timing:
            frame = self._mixer.presented_audio_frame()
            if frame is not None:
                return max(0, frame - self._frame_origin) / self._mixer.sample_rate
        audio_clock = self._mixer_processed_audio_seconds()
        if audio_clock is None or self._audio_clock_origin is None:
            return None
        return max(0.0, audio_clock - self._audio_clock_origin)

    def _reset_audio_clock_origin(self) -> None:
        self._audio_clock_origin = self._mixer_processed_audio_seconds()

    def _mixer_is_audio_ready(self) -> bool:
        accessor = getattr(self._mixer, "is_audio_ready", None)
        if not callable(accessor):
            return True
        try:
            return bool(accessor())
        except Exception:
            logger.exception("Could not query realtime audio readiness")
            return False

    def _mixer_processed_audio_seconds(self) -> float | None:
        accessor = getattr(self._mixer, "processed_audio_seconds", None)
        if not callable(accessor):
            return None
        value = accessor()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0.0
        ):
            return None
        return float(value)

    def _mixer_output_latency_seconds(self) -> float | None:
        accessor = getattr(self._mixer, "output_latency_seconds", None)
        if not callable(accessor):
            return None
        value = accessor()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0.0
        ):
            return None
        return float(value)

    def _upcoming_pitches(self, position: float) -> tuple[int, ...]:
        index = bisect_left(self._starts, (position, ""))
        # The 1 s/64-note horizon bounds per-tick work independently of score size.
        pitches = [note.pitch for note in self._notes[index:index + 64]
                   if note.start_time <= position + max(1.0, self._playback_rate)]
        if position == self._position:
            pitches.extend(note.pitch for note in self._notes[max(0, index - 24):index]
                           if note.start_time + sounding_duration(note) > position)
        return tuple(dict.fromkeys(pitches))

    def _prefetch_source(self, position: float) -> tuple[int, ...]:
        pitches = self._upcoming_pitches(position)
        prepare = getattr(self._source, 'prefetch_pitches', None)
        if callable(prepare):
            prepare(pitches)
        return pitches

    def _prepare_samples(self, *, resume_existing: bool = False) -> None:
        self.preparation_timed_out = False
        self.preparation_wait_ms = 0.0
        self._preparation_resume = resume_existing
        pitches = self._prefetch_source(self._position)
        # A seek can land inside a long sustain whose start is far older than
        # the local lookahead. Scan held notes only on preparation, never ticks.
        held = []
        for note in self._notes:
            if note.start_time >= self._position:
                break
            if note.start_time + sounding_duration(note) > self._position:
                held.append(note.pitch)
        self._preparation_pitches = tuple(dict.fromkeys((*pitches, *held)))
        prepare = getattr(self._source, 'prefetch_pitches', None)
        if held and callable(prepare):
            prepare(self._preparation_pitches)
        ready = getattr(self._source, 'ready_for_pitches', None)
        if callable(ready) and not ready(self._preparation_pitches):
            self._preparation_started = perf_counter()
            self._preparation_deadline = self._preparation_started + .08
            self._set_state(PlaybackState.PREPARING)
            self._preparation_timer.start()
            return
        self._prime_notes_at_position()
        self._begin_preparing(resume_existing=resume_existing)

    def _poll_sample_preparation(self) -> None:
        if self._state is not PlaybackState.PREPARING:
            self._preparation_timer.stop()
            return
        ready = self._source.ready_for_pitches(self._preparation_pitches)
        now = perf_counter()
        if not ready and now < self._preparation_deadline:
            return
        self._preparation_timer.stop()
        self.preparation_wait_ms = (now - self._preparation_started) * 1000
        self.preparation_timed_out = not ready
        if not ready:
            logger.warning('Sample preparation timed out after %.1f ms; realtime fallback remains enabled', self.preparation_wait_ms)
        try:
            self._prime_notes_at_position()
            self._begin_preparing(resume_existing=self._preparation_resume)
        except Exception as error:
            self._stop(ReleaseMode.STOP, preserve_pause=False)
            logger.exception('Could not finish sample preparation')
            self.preparationFailed.emit(str(error))

    def _begin_preparing(self, *, resume_existing: bool = False) -> None:
        self._set_state(PlaybackState.PREPARING)
        try:
            self._start_audio()
            self._output_reset = False
            if resume_existing:
                self._mixer.resume_audio()  # type: ignore[attr-defined]
        except Exception:
            self._stop(ReleaseMode.STOP, preserve_pause=False)
            raise
        if self._mixer_is_audio_ready():
            self._enter_playing()

    def _enter_playing(self) -> None:
        if self._state is not PlaybackState.PREPARING:
            return
        self._started_at = self._clock()
        self._render_position = self._position
        self._reset_audio_clock_origin()
        self._is_paused = False
        self._set_state(PlaybackState.PLAYING)
        self._timer.start()
        self.process_due_events()

    def _on_audio_ready_changed(self, ready: bool) -> None:
        if bool(ready) and self._state is PlaybackState.PREPARING and not self._preparation_timer.isActive():
            self._enter_playing()

    def _prime_notes_at_position(self) -> None:
        position = self._position
        if self._frame_timing:
            self._frame_origin = self._mixer.rendered_frames
            self._last_presentation = position
            self._completion_frame = self._frame_origin
        self._next_note_index = bisect_left(self._starts, (position, ""))
        for note in self._notes[: self._next_note_index]:
            if note.start_time + sounding_duration(note) > position:
                self._queue_note(note)
        while (
            self._next_note_index < len(self._notes)
            and self._notes[self._next_note_index].start_time <= position
        ):
            self._queue_note(self._notes[self._next_note_index])
            self._next_note_index += 1
        if self._frame_timing:
            self._plan_audio_frames()
        self._fade_initial_seek = False

    def _plan_audio_frames(self) -> None:
        # One real-time second survives the supported 500 ms GUI stall with
        # headroom. Limit producer work and retained sample tokens to 512 notes.
        rendered = self._mixer.rendered_frames
        position = self._position + max(0, rendered - self._frame_origin) / self._mixer.sample_rate * self._playback_rate
        if position < self._last_prefetch or position - self._last_prefetch >= .25:
            self._prefetch_source(position)
            self._last_prefetch = position
        horizon = position + self._playback_rate
        prepared = 0
        while self._next_note_index < len(self._notes) and len(self._active_voice_ids) < 512 and prepared < 512:
            note = self._notes[self._next_note_index]
            if note.start_time > horizon:
                break
            self._next_note_index += 1
            prepared += 1
            if note.start_time + sounding_duration(note) <= position:
                # Beyond the bounded horizon: an expired note must not restart
                # its attack late. Held notes resume at their actual sample age.
                logger.warning('Expired playback note after scheduler underrun: %s', note.id)
                continue
            self._queue_note(note)

    def _has_playable_note_at_position(self) -> bool:
        position = self._position
        return any(
            note.start_time <= position < note.start_time + sounding_duration(note)
            or note.start_time >= position
            for note in self._notes
        )

    def _queue_note(self, note: Note) -> None:
        voice_id = self._voice_id(note)
        if self._frame_timing:
            sample_rate = self._mixer.sample_rate
            start = self._frame_origin + round((note.start_time - self._position) / self._playback_rate * sample_rate)
            end = self._frame_origin + round((note.start_time + sounding_duration(note) - self._position) / self._playback_rate * sample_rate)
            current = self._mixer.rendered_frames
            age = max(0, current - start) / sample_rate
            self._active_voice_ids.add(voice_id)
            try:
                with self._mixer.at_audio_frame(max(current, start)):
                    self._source.start_voice(voice_id, note.pitch, note.velocity)
                    if age:
                        if self._fade_initial_seek:
                            self._mixer.seek_voice(voice_id, age, fade_in=True)
                        else:
                            self._mixer.seek_voice(voice_id, age)
                with self._mixer.at_audio_frame(max(current, end)):
                    self._release_note(note, voice_id)
            except Exception:
                self._active_voice_ids.discard(voice_id)
                logger.exception('Could not prepare timed playback note %s', note.id)
            return
        try:
            self._source.start_voice(  # type: ignore[attr-defined]
                voice_id, note.pitch, note.velocity
            )
            age = max(0.0, self._position - note.start_time)
            seek_voice = getattr(self._mixer, 'seek_voice', None)
            if age and callable(seek_voice):
                seek_voice(voice_id, age)
        except Exception:
            # One malformed or temporarily unavailable voice must not escape a
            # transport callback and take down the whole playback session.
            logger.exception(
                "Skipping playback note id=%r pitch=%r",
                note.id,
                note.pitch,
            )
            return
        self._active_voice_ids.add(voice_id)
        heappush(self._pending_releases, (note.start_time + sounding_duration(note), voice_id, note))

    def _release_note(self, note: Note, voice_id: str) -> None:
        release_mode = getattr(note, "release_mode", "natural")
        if release_mode == "cut":
            self._source.note_off(voice_id, ReleaseMode.STOP)  # type: ignore[attr-defined]
            return
        if release_mode == "custom":
            release_ms = getattr(note, "release_ms", None)
            if release_ms is not None:
                self._source.note_off(  # type: ignore[attr-defined]
                    voice_id,
                    ReleaseMode.NATURAL,
                    release_seconds=float(release_ms) / 1000.0,
                )
                return
        self._source.note_off(voice_id, ReleaseMode.NATURAL)  # type: ignore[attr-defined]

    def _set_state(self, state: PlaybackState) -> None:
        if self._state is state:
            self._is_playing = state is PlaybackState.PLAYING
            return
        was_playing = self._state is PlaybackState.PLAYING
        self._state = state
        self._is_playing = state is PlaybackState.PLAYING
        self.stateChanged.emit(state.value)
        if was_playing != self._is_playing:
            self.playingChanged.emit(self._is_playing)

    def _voice_id(self, note: Note) -> str:
        return f"{self._voice_prefix}:{self._generation}:{note.id}"

    def _start_audio(self) -> None:
        self._mixer.set_volume(self._volume)  # type: ignore[attr-defined]
        self._mixer.start_audio(  # type: ignore[attr-defined]
            buffer_milliseconds=self._buffer_milliseconds
        )

    def _finish_voice(self, voice_id: str) -> None:
        if voice_id not in self._active_voice_ids:
            return
        self._active_voice_ids.discard(voice_id)
        self._released_voice_ids.discard(voice_id)
        if self._frame_timing:
            self._completion_frame = max(self._completion_frame, self._mixer.rendered_frames)
            return
        if self.is_playing and self._next_note_index == len(self._notes) and not self._active_voice_ids:
            self._timer.stop()
            self._set_state(PlaybackState.STOPPED)
            self.finished.emit()

    def _finish_if_presented(self) -> None:
        if self._next_note_index != len(self._notes) or self._active_voice_ids:
            return
        frame = self._mixer.presented_audio_frame()
        end = self._frame_origin + round((self._project_end_seconds - self._position) / self._playback_rate * self._mixer.sample_rate)
        if frame is None or frame < max(end, self._completion_frame):
            return
        self._position = self._project_end_seconds
        self._timer.stop()
        self._set_state(PlaybackState.STOPPED)
        self.finished.emit()


def _note_snapshot(notes: Iterable[Note]) -> tuple[Note, ...]:
    return tuple(sorted(tuple(notes), key=lambda note: (note.start_time, note.id)))
