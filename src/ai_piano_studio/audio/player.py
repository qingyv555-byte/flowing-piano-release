from __future__ import annotations

import inspect
import logging
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from ai_piano_studio.audio.mixer import PianoVoiceMixer
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, ReleaseMode
from ai_piano_studio.audio.project_session import RealtimePlaybackSession
from ai_piano_studio.audio.realtime import RealtimeVoicePool
from ai_piano_studio.audio.source import PianoSoundSource
from ai_piano_studio.audio.sound_parameters import VelocitySource, velocity_response
from ai_piano_studio.audio.synth import SAMPLE_RATE, render_note, render_project, write_wav
from ai_piano_studio.domain.note import Note


logger = logging.getLogger(__name__)

AUDITION_DURATION = 0.8
MAX_AUDITION_CACHE_ENTRIES = 64
MAX_PROJECT_CACHE_ENTRIES = 4
_POSITION_POLL_MS = 33
_MEDIA_FADE_TICK_MS = 10


class _ProjectRenderSignals(QObject):
    succeeded = Signal(int)
    failed = Signal(int, str)


class _ProjectRenderJob(QRunnable):
    def __init__(
        self,
        generation: int,
        path: Path,
        notes: tuple[Note, ...],
        renderer: object | None,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._path = path
        self._notes = notes
        self._renderer = renderer
        self._cancelled = Event()
        self.signals = _ProjectRenderSignals()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(self) -> None:
        try:
            renderer = self._renderer
            rendered = (
                _render_project_with_cancellation(renderer, self._notes, self.is_cancelled)
                if renderer is not None
                else _render_default_project_with_cancellation(
                    self._notes, self.is_cancelled
                )
            )
            if self.is_cancelled():
                self.signals.succeeded.emit(self._generation)
                return
            if renderer is None:
                write_wav(self._path, rendered)
            else:
                write_wav(
                    self._path,
                    rendered,
                    channels=renderer.output_channels,
                )
            if self.is_cancelled():
                self._path.unlink(missing_ok=True)
                self.signals.succeeded.emit(self._generation)
                return
            self.signals.succeeded.emit(self._generation)
        except Exception as error:
            logger.exception("Project playback render failed")
            self.signals.failed.emit(self._generation, str(error))


def _render_project_with_cancellation(
    renderer: object, notes: tuple[Note, ...], should_cancel: Callable[[], bool]
):
    cancellable_render = getattr(renderer, "render_project_cancellable", None)
    if callable(cancellable_render):
        return cancellable_render(notes, should_cancel)
    return renderer.render_project(notes)


def _render_default_project_with_cancellation(
    notes: tuple[Note, ...], should_cancel: Callable[[], bool]
):
    parameters = inspect.signature(render_project).parameters.values()
    supports_cancellation = any(
        parameter.name == "should_cancel"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if supports_cancellation:
        return render_project(notes, should_cancel=should_cancel)
    return render_project(notes)


class AudioPlayer(QObject):
    """Play projects and auditions through the selected piano source.

    Source-backed projects stream through the realtime mixer. The source-less
    compatibility path retains rendered QMediaPlayer playback.
    """

    positionChanged = Signal(float)
    playingChanged = Signal(bool)
    preparingChanged = Signal(bool)
    playbackFailed = Signal(str)
    temporaryPlayingChanged = Signal(bool)
    temporaryPositionChanged = Signal(float)
    temporaryFinished = Signal()
    # Control-plane notification only; the mixer remains the source of whether
    # this accepted audition is actually sounding or has finished its release.
    auditionStarted = Signal(str)
    velocity_response_value = .5

    def __init__(
        self,
        parent=None,
        *,
        clock: Callable[[], float] | None = None,
        mixer: PianoVoiceMixer | None = None,
        piano_source: PianoSoundSource | None = None,
    ) -> None:
        super().__init__(parent)
        self._output = QAudioOutput(self)
        self._media = QMediaPlayer(self)
        self._media.setAudioOutput(self._output)
        self._media_is_available = True
        self._accept_project_render_callbacks = True
        self._devices = QMediaDevices(self)
        self._temp_dir = Path(tempfile.mkdtemp(prefix="ai-piano-player-"))
        source_mixer = getattr(piano_source, "mixer", None)
        self._mixer = (
            mixer
            if mixer is not None
            else source_mixer
            if source_mixer is not None
            else PianoVoiceMixer(parent=self)
        )
        self.piano_source = piano_source
        self._native_project_engine = None
        self._voice_pool = RealtimeVoicePool(self._mixer, self)
        self._next_source_voice_id = 1
        self._source_audition_ids: set[int] = set()
        voice_finished = getattr(self._mixer, 'voiceFinished', None)
        if voice_finished is not None:
            voice_finished.connect(self._on_audition_voice_finished)
        self._project_session: RealtimePlaybackSession | None = None
        self._project_session_notes: tuple[Note, ...] | None = None
        self._temporary_session: RealtimePlaybackSession | None = None
        self._temporary_session_notes: tuple[Note, ...] | None = None
        self._realtime_paused = False
        self._project_key: tuple[Note, ...] | None = None
        self._project_source_snapshot: object | None = None
        self._project_path: Path | None = None
        self._project_paths: list[Path] = []
        self._project_render_index = 0
        self._project_render_generation = 0
        self._project_render_jobs: set[_ProjectRenderJob] = set()
        self._pending_project_key: tuple[Note, ...] | None = None
        self._pending_project_source_snapshot: object | None = None
        self._pending_project_path: Path | None = None
        self._play_requested = False
        self._requested_project_position_ms = 0
        self._audition_cache: OrderedDict[
            tuple[int, int, int], tuple[int, ...]
        ] = OrderedDict()
        self._clock = clock or time.monotonic
        self._volume = 1.0
        self._playback_rate = 1.0
        self._media_fade_started_at: float | None = None
        self._media_fade_duration = 0.0
        self._media_fade_start_gain: float | None = None
        self._media_fade_start_master_volume: float | None = None
        self._media_fade_mode: ReleaseMode | None = None
        self._media_stop_settled = False
        self._pending_after_stopped: Callable[[], None] | None = None
        temp_dir = self._temp_dir
        voice_pool = self._voice_pool

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POSITION_POLL_MS)
        self._poll_timer.timeout.connect(self._poll_position)
        self._media_fade_timer = QTimer(self)
        self._media_fade_timer.setInterval(_MEDIA_FADE_TICK_MS)
        self._media_fade_timer.timeout.connect(self.process_media_fade)
        self._media.playbackStateChanged.connect(self._on_state_changed)
        self._media.errorOccurred.connect(self._on_media_error)
        self._media.destroyed.connect(self._on_media_destroyed)
        self._devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self.destroyed.connect(
            lambda _destroyed=None: self._dispose_player_audio(voice_pool, temp_dir)
        )

    def is_playing(self) -> bool:
        if self.piano_source is not None:
            return bool(
                self._project_session is not None
                and self._project_session.is_playing
            )
        return self._media.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def position_seconds(self) -> float:
        if self.piano_source is not None and self._project_session is not None:
            return self._project_session.position_seconds
        return self._media.position() / 1000.0

    def is_preparing(self) -> bool:
        return any(session is not None and session.is_preparing
                   for session in (self._project_session, self._temporary_session))

    def prefetch_notes(self, notes: tuple[Note, ...], position: float = 0.0) -> None:
        prepare = getattr(self.piano_source, 'prefetch_pitches', None)
        if callable(prepare):
            # Editor snapshots are already ordered. Stop after the local horizon.
            pitches = []
            for note in notes:
                if note.start_time >= position:
                    pitches.append(note.pitch)
                if len(pitches) >= 64:
                    break
            prepare(pitches)

    def play_notes(self, notes: tuple[Note, ...]) -> None:
        if not notes:
            raise ValueError("cannot play an empty note list")
        if self.piano_source is not None:
            self._play_realtime_notes(notes)
            return
        self._cancel_media_fade()
        self._media_stop_settled = False
        self._bind_default_output()
        self._play_requested = True
        source_snapshot = (
            self.piano_source.snapshot_renderer()
            if self.piano_source is not None
            else None
        )
        source_identity = getattr(source_snapshot, "selection", source_snapshot)
        if notes == self._project_key and source_identity == self._project_source_snapshot:
            self._media.setPosition(self._requested_project_position_ms)
            self._media.play()
            return
        if (
            notes == self._pending_project_key
            and source_identity == self._pending_project_source_snapshot
        ):
            return
        self._queue_project_render(notes, source_snapshot, source_identity)

    def play_notes_at(self, notes: tuple[Note, ...], position: float) -> None:
        if self.piano_source is not None and self._project_session is None and position > 0:
            self._play_realtime_notes(notes, initial_position=position)
        else:
            self.play_notes(notes)

    def _play_realtime_notes(self, notes: tuple[Note, ...], *, initial_position: float = 0.0) -> None:
        session = self._project_session
        if session is None:
            if self._native_project_engine is None:
                session = RealtimePlaybackSession(notes, VelocitySource(self.piano_source, lambda: self.velocity_response_value), self._mixer, clock=self._clock,
                                                  initial_position=initial_position)
            else:
                from .native_playback import NativePlaybackSession

                session = NativePlaybackSession(notes, self._native_project_engine, self._mixer, clock=self._clock,
                                                initial_position=initial_position)
            session.setParent(self)
            session.positionChanged.connect(self.positionChanged.emit)
            session.playingChanged.connect(self.playingChanged.emit)
            session.stateChanged.connect(lambda _state: self.preparingChanged.emit(self.is_preparing()))
            session.preparationFailed.connect(self.playbackFailed.emit)
            session.set_volume(self._volume)
            session.set_playback_rate(self._playback_rate)
            self._project_session = session
            self._project_session_notes = notes
            self._realtime_paused = False
            session.start()
            return
        if notes != self._project_session_notes:
            self._project_session_notes = notes
            self._realtime_paused = False
            session.replace_notes(notes)
            return
        if session.is_preparing:
            return
        if self._realtime_paused:
            self._realtime_paused = False
            session.resume()
        elif not session.is_playing:
            session.seek(0.0)

    def use_native_project_engine(self, engine) -> None:
        old = self._project_session
        if old is not None:
            old.stop(ReleaseMode.SAFE)
            old.deleteLater()
        self._project_session = None
        self._project_session_notes = None
        self._realtime_paused = False
        self._native_project_engine = engine

    def play_temporary_notes(self, notes: tuple[Note, ...]) -> None:
        """Play an isolated note snapshot without changing project transport state."""
        if not isinstance(notes, tuple) or not notes or any(
            not isinstance(note, Note) for note in notes
        ):
            raise ValueError("notes must be a non-empty tuple of Note")
        if self.piano_source is None:
            raise RuntimeError("temporary playback requires a realtime piano source")

        self.stop_temporary_notes(ReleaseMode.STOP)
        if self._project_session is not None:
            self._project_session.stop(ReleaseMode.STOP)
            self._realtime_paused = False

        if self._native_project_engine is not None:
            from .native_playback import NativePlaybackSession
            session = NativePlaybackSession(notes, self._native_project_engine, self._mixer, clock=self._clock)
        else:
            session = RealtimePlaybackSession(
                notes, VelocitySource(self.piano_source, lambda: self.velocity_response_value), self._mixer,
                clock=self._clock, voice_prefix="temporary")
        session.setParent(self)
        session.positionChanged.connect(self.temporaryPositionChanged.emit)
        session.playingChanged.connect(self.temporaryPlayingChanged.emit)
        session.stateChanged.connect(lambda _state: self.preparingChanged.emit(self.is_preparing()))
        session.preparationFailed.connect(self.playbackFailed.emit)
        session.finished.connect(self._on_temporary_finished)
        session.set_volume(self._volume)
        session.set_playback_rate(self._playback_rate)
        self._temporary_session = session
        self._temporary_session_notes = notes
        session.start()

    def stop_temporary_notes(
        self, mode: ReleaseMode = ReleaseMode.STOP
    ) -> None:
        """Stop only the temporary selection session, never audition voices."""
        session = self._temporary_session
        if session is None:
            return
        session.stop(mode)
        if mode is ReleaseMode.STOP:
            self._temporary_session = None
            self._temporary_session_notes = None
            session.deleteLater()

    def finish_temporary_notes(self) -> None:
        """Release a naturally finished temporary session without resetting audio."""
        session = self._temporary_session
        if session is None:
            return
        if session.is_playing or session.is_preparing:
            self.stop_temporary_notes(ReleaseMode.STOP)
            return
        self._temporary_session = None
        self._temporary_session_notes = None
        try:
            session.finished.disconnect(self._on_temporary_finished)
        except (RuntimeError, TypeError):
            pass
        session.deleteLater()

    def _on_temporary_finished(self) -> None:
        self.temporaryFinished.emit()

    def _queue_project_render(
        self,
        notes: tuple[Note, ...],
        source_snapshot: object | None,
        source_identity: object | None,
    ) -> None:
        self._cancel_project_render_jobs()
        self._project_render_generation += 1
        generation = self._project_render_generation
        self._project_render_index += 1
        path = self._temp_dir / f"project-mix-{self._project_render_index}.wav"
        self._pending_project_key = notes
        self._pending_project_source_snapshot = source_identity
        self._pending_project_path = path
        job = _ProjectRenderJob(generation, path, notes, source_snapshot)
        job.signals.succeeded.connect(
            lambda completed_generation: self._on_project_rendered(job, completed_generation)
        )
        job.signals.failed.connect(
            lambda failed_generation, message: self._on_project_render_failed(
                job, failed_generation, message
            )
        )
        self._project_render_jobs.add(job)
        QThreadPool.globalInstance().start(job)

    def _on_project_rendered(
        self, job: _ProjectRenderJob, generation: int
    ) -> None:
        self._project_render_jobs.discard(job)
        if not self._accept_project_render_callbacks:
            self._discard_project_path(job._path)
            return
        if generation != self._project_render_generation or job._path != self._pending_project_path:
            self._discard_project_path(job._path)
            return
        self._project_path = job._path
        self._project_paths.append(job._path)
        self._project_key = self._pending_project_key
        self._project_source_snapshot = self._pending_project_source_snapshot
        self._pending_project_key = None
        self._pending_project_source_snapshot = None
        self._pending_project_path = None
        self._media.setSource(QUrl.fromLocalFile(str(self._project_path)))
        self._prune_project_cache()
        if self._play_requested:
            self._media.setPosition(self._requested_project_position_ms)
            self._media.play()

    def _on_project_render_failed(
        self, job: _ProjectRenderJob, generation: int, message: str
    ) -> None:
        self._project_render_jobs.discard(job)
        if not self._accept_project_render_callbacks:
            return
        if generation != self._project_render_generation:
            return
        self._pending_project_key = None
        self._pending_project_source_snapshot = None
        self._pending_project_path = None
        self._play_requested = False
        self._report_playback_failure(message or "project render failed")

    def _invalidate_project_render_callbacks(self, _destroyed=None) -> None:
        self._accept_project_render_callbacks = False

    def _on_media_destroyed(self, _destroyed=None) -> None:
        self._media_is_available = False
        self._invalidate_project_render_callbacks()
        try:
            self._devices.audioOutputsChanged.disconnect(self._on_audio_outputs_changed)
        except (RuntimeError, TypeError):
            pass

    def _dispose_player_audio(
        self, pool: RealtimeVoicePool, temp_dir: Path
    ) -> None:
        self._invalidate_project_render_callbacks()
        self._cancel_project_render_jobs()
        _dispose_player_audio(pool, temp_dir)

    def _cancel_project_render_jobs(self) -> None:
        for job in self._project_render_jobs:
            job.cancel()

    def _discard_project_path(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove stale project audio render", exc_info=True)

    def _bind_default_output(self, *, preserve_playback: bool = False) -> None:
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("no default audio output is available")
        if self._output.device() == device:
            return
        position = self._media.position()
        was_playing = self.is_playing()
        self._output.setDevice(device)
        if preserve_playback:
            self._media.setPosition(position)
            if was_playing:
                self._media.play()

    def _on_audio_outputs_changed(self) -> None:
        if not self._media_is_available:
            return
        try:
            self._bind_default_output(preserve_playback=True)
        except RuntimeError as error:
            self._report_playback_failure(str(error))

    def _on_media_error(self, _error, error_string: str) -> None:
        self._report_playback_failure(error_string or "media playback failed")

    def _report_playback_failure(self, message: str) -> None:
        device = self._output.device()
        description = "<none>" if device.isNull() else device.description()
        source = self._media.source()
        logger.error(
            "Project playback failed: %s; output=%s; source=%s; media_status=%s; playback_state=%s",
            message,
            description,
            source.toLocalFile() or source.toString(),
            self._media.mediaStatus(),
            self._media.playbackState(),
        )
        self.playbackFailed.emit(message)

    def _prune_project_cache(self) -> None:
        while len(self._project_paths) > MAX_PROJECT_CACHE_ENTRIES:
            stale_path = self._project_paths.pop(0)
            if stale_path == self._project_path:
                continue
            try:
                stale_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not remove stale project audio cache", exc_info=True)

    def pause(self) -> None:
        self._play_requested = False
        if self.piano_source is not None and self._project_session is not None:
            self._realtime_paused = True
            self._project_session.pause()
            return
        self._media.pause()

    def seek(self, seconds: float) -> None:
        if self.piano_source is not None and self._project_session is not None:
            session = self._project_session
            start_playback = session.is_playing or session.is_preparing
            if not start_playback:
                self._realtime_paused = True
            session.seek(seconds, start_playback=start_playback)
            return
        position_ms = max(0, round(float(seconds) * 1000))
        self._requested_project_position_ms = position_ms
        self._media_stop_settled = False
        self._media.setPosition(position_ms)
        self.positionChanged.emit(position_ms / 1000.0)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self.piano_source is not None:
            self._mixer.set_volume(self._volume)
            if self._project_session is not None:
                self._project_session.set_volume(self._volume)
            if self._temporary_session is not None:
                self._temporary_session.set_volume(self._volume)
            return
        self.process_media_fade()
        if self._media_fade_started_at is None:
            self._output.setVolume(self._volume)

    @property
    def volume(self) -> float:
        return self._volume

    def set_playback_rate(self, rate: float) -> None:
        self._playback_rate = max(0.5, min(2.0, float(rate)))
        if self.piano_source is not None:
            if self._project_session is not None:
                self._project_session.set_playback_rate(self._playback_rate)
            if self._temporary_session is not None:
                self._temporary_session.set_playback_rate(self._playback_rate)
            return
        self._media.setPlaybackRate(self._playback_rate)

    def stop(
        self,
        mode: ReleaseMode = ReleaseMode.STOP,
        *,
        after_stopped: Callable[[], None] | None = None,
    ) -> None:
        self._play_requested = False
        if self.piano_source is not None:
            self._realtime_paused = False
            if self._project_session is not None:
                self._project_session.stop(mode)
            if after_stopped is not None:
                after_stopped()
            return
        self._cancel_project_render_jobs()
        self._project_render_generation += 1
        self._pending_project_key = None
        self._pending_project_source_snapshot = None
        self._pending_project_path = None
        if self._media_fade_started_at is not None:
            self._pending_after_stopped = after_stopped
            if mode is ReleaseMode.STOP and self._media_fade_mode is not ReleaseMode.STOP:
                self._media_fade_start_gain = self._current_media_fade_gain()
                self._media_fade_start_master_volume = self._volume
                self._media_fade_mode = mode
                self._media_fade_duration = ACOUSTIC_PIANO_V2.stop_fade_seconds
                self._media_fade_started_at = self._clock()
                self.process_media_fade()
            return

        self._pending_after_stopped = after_stopped
        if not self.is_playing():
            if self._media_stop_settled:
                self._deliver_stop_callback()
                return
            self._finish_media_stop()
            return

        self._media_fade_mode = mode
        self._media_fade_duration = (
            ACOUSTIC_PIANO_V2.stop_fade_seconds
            if mode is ReleaseMode.STOP
            else ACOUSTIC_PIANO_V2.safe_release_seconds
        )
        self._media_fade_start_gain = self._volume
        self._media_fade_start_master_volume = self._volume
        self._media_fade_started_at = self._clock()
        self._media_fade_timer.start()
        self.process_media_fade()

    def process_media_fade(self) -> None:
        started_at = self._media_fade_started_at
        if started_at is None:
            return
        elapsed = max(0.0, self._clock() - started_at)
        progress = (
            1.0
            if elapsed + 1e-9 >= self._media_fade_duration
            else elapsed / self._media_fade_duration
        )
        self._output.setVolume(self._current_media_fade_gain())
        if progress >= 1.0:
            self._finish_media_stop()

    def _current_media_fade_gain(self) -> float:
        started_at = self._media_fade_started_at
        start_gain = self._media_fade_start_gain
        start_master_volume = self._media_fade_start_master_volume
        if (
            started_at is None
            or start_gain is None
            or start_master_volume is None
        ):
            return self._volume
        if start_master_volume == 0.0:
            elapsed = max(0.0, self._clock() - started_at)
            progress = min(1.0, elapsed / self._media_fade_duration)
            return self._volume * (1.0 - progress)
        elapsed = max(0.0, self._clock() - started_at)
        progress = (
            1.0
            if elapsed + 1e-9 >= self._media_fade_duration
            else elapsed / self._media_fade_duration
        )
        return start_gain * (self._volume / start_master_volume) * (1.0 - progress)

    def _cancel_media_fade(self) -> None:
        self._media_fade_timer.stop()
        self._media_fade_started_at = None
        self._media_fade_duration = 0.0
        self._media_fade_start_gain = None
        self._media_fade_start_master_volume = None
        self._media_fade_mode = None
        self._pending_after_stopped = None
        self._output.setVolume(self._volume)

    def _finish_media_stop(self) -> None:
        self._media_fade_timer.stop()
        self._media_fade_started_at = None
        self._media_fade_duration = 0.0
        self._media_fade_start_gain = None
        self._media_fade_start_master_volume = None
        self._media_fade_mode = None
        self._media.stop()
        self._poll_timer.stop()
        self._output.setVolume(self._volume)
        self._media.setPosition(0)
        self._requested_project_position_ms = 0
        self._media_stop_settled = True
        self.positionChanged.emit(0.0)
        self._deliver_stop_callback()

    def _deliver_stop_callback(self) -> None:
        callback = self._pending_after_stopped
        self._pending_after_stopped = None
        if callback is not None:
            try:
                callback()
            except Exception:
                logger.exception("Media stop completion callback failed")

    def audition(
        self, pitch: int, velocity: int = 75, duration: float | None = None
    ) -> int:
        try:
            if self.piano_source is not None:
                self._voice_pool.ensure_audio_started()
                voice_id = self._next_source_voice_id
                self._next_source_voice_id += 1
                self.piano_source.start_voice(
                    f"audition:{voice_id}", int(pitch), velocity_response(int(velocity), self.velocity_response_value)
                )
                self._source_audition_ids.add(voice_id)
                self.auditionStarted.emit(f"audition:{voice_id}")
                return voice_id
            bounded_duration = (
                ACOUSTIC_PIANO_V2.render_hold_seconds
                if duration is None
                else min(4.0, max(0.08, float(duration)))
            )
            duration_ms = round(bounded_duration * 1000)
            cache_key = (int(pitch), int(velocity), duration_ms)
            source = self._audition_cache.pop(cache_key, None)
            if source is None:
                source = tuple(render_note(pitch, bounded_duration, int(velocity)))
            voice_id = self._voice_pool.start(
                int(pitch),
                int(velocity),
                source,
                source_sample_rate=SAMPLE_RATE,
                owner_tag="audition",
            )
            self._audition_cache[cache_key] = source
            self._prune_audition_cache()
            self.auditionStarted.emit(f"audition:{voice_id}")
            return voice_id
        except Exception:
            logger.exception("Note audition failed")
            return -1

    def release_audition(
        self, voice_id: int, mode: ReleaseMode = ReleaseMode.NATURAL
    ) -> None:
        if self.piano_source is not None:
            self.piano_source.note_off(f"audition:{int(voice_id)}", mode)
        else:
            self._voice_pool.release(voice_id, mode)

    def stop_audition(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        if self.piano_source is not None:
            for voice_id in tuple(self._source_audition_ids):
                self.piano_source.note_off(f'audition:{voice_id}', mode)
        else:
            self._voice_pool.release_all(mode)

    def _on_audition_voice_finished(self, voice_id: str) -> None:
        prefix, separator, identifier = voice_id.partition(':')
        if prefix == 'audition' and separator and identifier.isdigit():
            self._source_audition_ids.discard(int(identifier))

    def _prune_audition_cache(self) -> None:
        while len(self._audition_cache) > MAX_AUDITION_CACHE_ENTRIES:
            self._audition_cache.popitem(last=False)

    def _poll_position(self) -> None:
        position_ms = max(0, round(self._media.position()))
        self._requested_project_position_ms = position_ms
        self.positionChanged.emit(position_ms / 1000.0)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
            if state == QMediaPlayer.PlaybackState.StoppedState:
                self.positionChanged.emit(0.0)
        self.playingChanged.emit(self.is_playing())


def _remove_temp_directory(path: Path) -> None:
    try:
        for child in path.iterdir():
            child.unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        logger.debug("Could not remove audio player temp directory", exc_info=True)


def _dispose_player_audio(pool: RealtimeVoicePool, temp_dir: Path) -> None:
    try:
        pool.dispose()
    except Exception:
        logger.exception("Could not dispose audition voices")
    _remove_temp_directory(temp_dir)
