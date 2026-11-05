from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from heapq import heappop, heappush, heapify
from dataclasses import dataclass
import logging
from math import cos, exp, isfinite, pi, sin
from threading import RLock, local
from time import monotonic
from typing import Callable, Sequence
from weakref import proxy

import numpy as np
from PySide6.QtCore import QEventLoop, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, PianoVoiceProfile, ReleaseMode
from ai_piano_studio.audio.visualizer import PcmSnapshotTap
from ai_piano_studio.audio.output_thread import ThreadedAudioSink
from ai_piano_studio.audio.sound_gain import GainRamp, scale_pcm
from ai_piano_studio.audio.sound_parameters import SoundProcessor


logger = logging.getLogger(__name__)

_INT16_SCALE = 32_767
# +6 dB over the former 2x stage: ordinary sampled notes retain their
# velocity dynamics; only strong/polyphonic peaks reach the soft limiter.
MASTER_OUTPUT_GAIN = 4.0
# Audition starts without a project session. Bound its queue instead of using
# the platform default; project playback can still request its larger budget.
AUDITION_BUFFER_MILLISECONDS = 40
_MAX_VOICES = 24
_QUIET_GAIN = 0.05
_STOP_FADE_SECONDS = 0.012
_MAX_RELEASE_SECONDS = 5.0
_PULL_AVAILABLE_BYTES = 4_096


@dataclass(slots=True)
class _PCMContributionState:
    samples: memoryview | tuple[int, ...]
    sample_rate: int
    channels: int
    gain: float
    pan: float
    looping: bool = False
    read_position: float = 0.0
    sample_values: np.ndarray | None = None
    source_step: float = 0.0
    left_pan_gain: float = 0.0
    right_pan_gain: float = 0.0


@dataclass(frozen=True, slots=True)
class PCMContribution:
    samples: memoryview | tuple[int, ...]
    sample_rate: int
    channels: int
    gain: float
    pan: float
    looping: bool = False


@dataclass(slots=True)
class PCMVoice:
    voice_id: str
    pitch: int
    velocity: int
    contributions: tuple[_PCMContributionState, ...]
    current_gain: float
    target_gain: float
    envelope_mode: ReleaseMode | None
    release_start: float | None
    release_start_frame: int | None
    release_seconds: float
    release_start_gain: float
    age: float
    owner_tag: str
    attack_step: float


@dataclass(frozen=True, slots=True)
class _StartVoice:
    voice_id: str
    pitch: int
    velocity: int
    contributions: tuple[PCMContribution, ...]
    owner_tag: str
    queued_at: float


@dataclass(frozen=True, slots=True)
class _ReleaseVoice:
    voice_id: str
    mode: ReleaseMode
    queued_at: float
    release_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _ReleaseAll:
    mode: ReleaseMode
    queued_at: float


@dataclass(frozen=True, slots=True)
class _SeekVoice:
    voice_id: str
    seconds: float
    fade_in: bool = False


class PianoMixIODevice(QIODevice):
    """Sequential device from which QAudioSink pulls mixed stereo frames."""

    def __init__(self, mixer: PianoVoiceMixer, parent: QObject | None = None) -> None:
        super().__init__(parent if parent is not None else mixer)
        self._mixer = proxy(mixer)
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:  # noqa: N802 - Qt virtual method
        return True

    def bytesAvailable(self) -> int:  # noqa: N802 - Qt virtual method
        return max(_PULL_AVAILABLE_BYTES, super().bytesAvailable())

    def atEnd(self) -> bool:  # noqa: N802 - Qt virtual method
        return False

    def readData(self, max_size: int) -> bytes:  # noqa: N802 - Qt virtual method
        try:
            # QIODevice permits short reads. Keep all PCM scratch allocations
            # bounded even if a backend requests a very large refill.
            max_size = min(max_size, 32768)
            frame_count = max(0, max_size // 4)
            payload = self._mixer._read_output_frames(frame_count)
            if len(payload) < max_size:
                payload += bytes(max_size - len(payload))
            return payload[:max_size]
        except Exception:
            logger.exception("Audio pull failed; returning silence")
            return bytes(max(0, max_size))

    def writeData(self, _data: bytes) -> int:  # noqa: N802 - Qt virtual method
        return -1


class PianoVoiceMixer(QObject):
    """Thread-safe command front end and deterministic PCM voice mixer."""

    audioReadyChanged = Signal(bool)
    stopFadeDelivered = Signal()
    voiceFinished = Signal(str)

    def __init__(
        self,
        *,
        profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        sample_rate: int = 48_000,
        max_voices: int = 24,
        clock: Callable[[], float] = monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        try:
            if not isinstance(profile, PianoVoiceProfile):
                raise ValueError("profile must be a PianoVoiceProfile")
            if (
                isinstance(sample_rate, bool)
                or not isinstance(sample_rate, int)
                or sample_rate <= 0
            ):
                raise ValueError("sample_rate must be a positive integer")
            if (
                isinstance(max_voices, bool)
                or not isinstance(max_voices, int)
                or max_voices <= 0
                or max_voices > _MAX_VOICES
            ):
                raise ValueError("max_voices must be an integer from 1 to 24")
            if not callable(clock):
                raise ValueError("clock must be callable")

            self._profile = profile
            self._sample_rate = sample_rate
            self._max_voices = max_voices
            self._clock = clock
            self._commands: deque[_StartVoice | _ReleaseVoice | _ReleaseAll] = deque()
            self._command_lock = RLock()
            self._capture = local()
            self._timed_commands = []
            self._command_sequence = 0
            self._state_lock = RLock()
            self._audio_lock = RLock()
            self._voices: list[PCMVoice] = []
            self._peak_voices = 0
            self._steal_count = 0
            self._steal_left = np.zeros(max(1, round(_STOP_FADE_SECONDS * sample_rate)))
            self._steal_right = np.zeros_like(self._steal_left)
            self._monitor_steal_left = np.zeros_like(self._steal_left)
            self._monitor_steal_right = np.zeros_like(self._steal_left)
            self._output_frame = 0
            self._native_stream = None
            self._native_tail = np.empty((0, 2), dtype='<i2')
            self._sample_gain = GainRamp()
            self._native_gain = GainRamp()
            self._sound_effects = SoundProcessor(self._sample_rate)
            self._analysis_effects = SoundProcessor(self._sample_rate)
            self._sink_frame_origin = 0
            self._stop_delivery_frame: int | None = None
            self._sink: QAudioSink | None = None
            self._audio_ready = False
            self._buffer_milliseconds: int | None = None
            self._volume = 1.0
            self._volume_configured = False
            self._visualizer_tap: PcmSnapshotTap | None = None
            self._device = PianoMixIODevice(self)
        except Exception:
            logger.exception("Failed to construct piano voice mixer")
            raise

    @property
    def active_voice_ids(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(voice.voice_id for voice in self._voices)

    @property
    def io_device(self) -> PianoMixIODevice:
        return self._device

    def voice_statistics(self):
        with self._state_lock:
            return {'active':len(self._voices), 'peak':self._peak_voices,
                    'steals':self._steal_count, 'limit':self._max_voices,
                    'stealFadeMs':_STOP_FADE_SECONDS*1000}

    def is_audio_ready(self) -> bool:
        """Return whether the output sink can currently consume PCM."""
        with self._audio_lock:
            return self._audio_ready and not (
                isinstance(self._sink, ThreadedAudioSink) and self._sink.is_closed
            )

    def set_visualizer_tap(self, tap: PcmSnapshotTap | None) -> None:
        if tap is not None and not isinstance(tap, PcmSnapshotTap):
            raise TypeError("tap must be a PcmSnapshotTap or None")
        self._visualizer_tap = tap

    def processed_audio_seconds(self) -> float | None:
        """Return the active output device's consumed PCM duration, if available."""
        with self._audio_lock:
            sink = self._sink
            if sink is None or isinstance(sink, ThreadedAudioSink) and sink.is_closed:
                return None
            processed_microseconds = getattr(sink, "processedUSecs", None)
            if not callable(processed_microseconds):
                return None
            return max(0.0, float(processed_microseconds()) / 1_000_000.0)

    def presented_audio_frame(self) -> int | None:
        """Map the sink's consumed duration into the mixer's persistent frame axis."""
        with self._audio_lock:
            seconds = self.processed_audio_seconds()
            if seconds is None:
                return None
            return self._sink_frame_origin + round(seconds * self._sample_rate)

    def output_latency_seconds(self) -> float | None:
        """Return the bounded sink latency budget for realtime transport alignment."""
        with self._audio_lock:
            sink = self._sink
            if sink is None:
                return None
            buffer_size = max(0, int(sink.bufferSize()))
            return buffer_size / (self._sample_rate * 4)

    def start_voice(
        self,
        voice_id: str,
        pitch: int,
        velocity: int,
        source: Sequence[int],
        *,
        source_sample_rate: int,
        owner_tag: str,
        pan: float = 0.0,
        looping: bool = False,
    ) -> None:
        """Compatibility wrapper for legacy mono, single-buffer callers."""
        try:
            self.start_contributions(
                voice_id,
                pitch,
                velocity,
                (
                    PCMContribution(
                        _immutable_pcm(source),
                        source_sample_rate,
                        1,
                        1.0,
                        pan,
                        looping,
                    ),
                ),
                owner_tag=owner_tag,
            )
        except Exception:
            logger.exception("Failed to queue piano voice %r", voice_id)
            raise

    def start_contributions(
        self,
        voice_id: str,
        pitch: int,
        velocity: int,
        contributions: tuple[PCMContribution, ...],
        *,
        owner_tag: str,
    ) -> None:
        try:
            if not isinstance(contributions, tuple) or not 1 <= len(contributions) <= 4:
                raise ValueError("contributions must contain from one to four PCM inputs")
            command = _StartVoice(
                voice_id=_validate_voice_id(voice_id),
                pitch=_validate_midi_value("pitch", pitch),
                velocity=_validate_midi_value("velocity", velocity),
                contributions=tuple(_validate_contribution(item) for item in contributions),
                owner_tag=_validate_owner_tag(owner_tag),
                queued_at=float(self._clock()),
            )
            self._enqueue_command(command)
        except Exception:
            logger.exception("Failed to queue piano contributions for voice %r", voice_id)
            raise

    def seek_voice(self, voice_id: str, seconds: float, *, fade_in: bool = False) -> None:
        """Advance a newly primed voice to its musical age before the next PCM pull."""
        if not isfinite(seconds) or seconds < 0:
            raise ValueError('voice age must be finite and nonnegative')
        self._enqueue_command(_SeekVoice(_validate_voice_id(voice_id), seconds, fade_in))

    @property
    def rendered_frames(self) -> int:
        with self._state_lock:
            return self._output_frame

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _enqueue_command(self, command) -> None:
        captured = getattr(self._capture, 'commands', None)
        if captured is not None:
            captured.append(command)
        else:
            with self._command_lock:
                self._commands.append(command)

    @contextmanager
    def at_audio_frame(self, frame: int):
        """Prepare on the producer thread, then atomically publish one timed batch.

        Sample lookup/decoding never executes in the PCM consumer. The session
        bounds the horizon and number of prepared voices.
        """
        if getattr(self._capture, 'commands', None) is not None:
            raise RuntimeError('nested audio command capture')
        commands = []
        self._capture.commands = commands
        try:
            yield
            with self._command_lock:
                for command in commands:
                    self._command_sequence += 1
                    heappush(self._timed_commands, (max(0, int(frame)), self._command_sequence, command))
        finally:
            self._capture.commands = None

    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode,
        *,
        release_seconds: float | None = None,
    ) -> None:
        try:
            valid_id = _validate_voice_id(voice_id)
            if not isinstance(mode, ReleaseMode):
                raise ValueError("mode must be a ReleaseMode")
            command = _ReleaseVoice(
                valid_id,
                mode,
                float(self._clock()),
                _validate_release_seconds(release_seconds),
            )
            self._enqueue_command(command)
        except Exception:
            logger.exception("Failed to queue note_off for voice %r", voice_id)
            raise

    def release_all(self, mode: ReleaseMode) -> None:
        try:
            if not isinstance(mode, ReleaseMode):
                raise ValueError("mode must be a ReleaseMode")
            command = _ReleaseAll(mode, float(self._clock()))
            with self._command_lock:
                self._commands.append(command)
        except Exception:
            logger.exception("Failed to queue release_all")
            raise

    def discard_voice_ids_and_reset_output(self, voice_ids: Sequence[str]) -> None:
        """Discard one transport's queued PCM and reset the device buffer."""
        try:
            ids = frozenset(_validate_voice_id(voice_id) for voice_id in voice_ids)
            if not ids:
                with self._state_lock:
                    with self._command_lock:
                        has_active_output = bool(self._voices) or any(
                            isinstance(command, _StartVoice) for command in self._commands
                        )
                if not has_active_output:
                    return
            with self._state_lock:
                with self._command_lock:
                    cancelled = [command.voice_id for command in self._commands
                                 if isinstance(command, _StartVoice) and command.voice_id in ids]
                    cancelled.extend(item[2].voice_id for item in self._timed_commands
                                     if isinstance(item[2], _StartVoice) and item[2].voice_id in ids)
                    self._discard_queued_voice_ids(ids)
                removed = tuple(voice for voice in self._voices if voice.voice_id in ids)
                self._voices = [voice for voice in self._voices if voice.voice_id not in ids]
            with self._audio_lock:
                had_output = self._sink is not None
                if self._sink is not None:
                    old_sink = self._sink
                    self._sink = None
                    self._set_audio_ready_locked(False)
                    old_sink.reset()
                    old_sink.stop()
                    old_sink.deleteLater()
            for voice in removed:
                self.voiceFinished.emit(voice.voice_id)
            for voice_id in cancelled:
                self.voiceFinished.emit(voice_id)
            with self._state_lock:
                with self._command_lock:
                    self._discard_queued_voice_ids(ids)
                    keep_output_running = bool(self._voices) or any(
                        isinstance(command, _StartVoice) for command in self._commands
                    )
            with self._audio_lock:
                if self._sink is None and keep_output_running and had_output:
                    self._start_audio_locked(self._buffer_milliseconds)
        except Exception:
            logger.exception("Failed to discard transport PCM")
            raise

    def _discard_queued_voice_ids(self, voice_ids: frozenset[str]) -> None:
        self._commands = deque(
            command
            for command in self._commands
            if not isinstance(command, (_StartVoice, _ReleaseVoice, _SeekVoice))
            or command.voice_id not in voice_ids
        )
        self._timed_commands = [item for item in self._timed_commands
                                if getattr(item[2], 'voice_id', None) not in voice_ids]
        heapify(self._timed_commands)

    def cancel_timed_voice_ids(self, voice_ids: Sequence[str]) -> None:
        """Retire prepared tokens without cutting voices already being rendered."""
        ids = frozenset(voice_ids)
        with self._state_lock:
            with self._command_lock:
                cancelled = [item[2].voice_id for item in self._timed_commands
                             if isinstance(item[2], _StartVoice) and item[2].voice_id in ids]
                self._timed_commands = [item for item in self._timed_commands
                                        if getattr(item[2], 'voice_id', None) not in ids]
                heapify(self._timed_commands)
        for voice_id in cancelled:
            self.voiceFinished.emit(voice_id)

    def mix_frames(self, frame_count: int) -> bytes:
        return self._mix_frames(frame_count, output_delivery=False)

    def attach_native_stream(self, stream) -> int:
        with self._state_lock:
            if self._native_stream is not None:
                raise RuntimeError('a native project stream is already attached')
            self._native_stream = stream
            return self._output_frame

    def set_sound_gains(self, sample_gain: float, native_gain: float) -> None:
        with self._state_lock:
            ramp = round(.02 * self._sample_rate) if self._output_frame else 0
            self._sample_gain.set(sample_gain, ramp)
            self._native_gain.set(native_gain, ramp)

    def set_sound_parameters(self, parameters, *, reset=False) -> None:
        with self._state_lock:
            for processor in (self._sound_effects, self._analysis_effects):
                if reset:
                    processor.clear()
                processor.set(parameters, immediate=not self._output_frame)

    def detach_native_stream(self, stream, *, fade: bool) -> None:
        with self._state_lock:
            if self._native_stream is not stream:
                return
            self._native_stream = None
            if fade:
                self._sound_effects.fade_out()
                self._analysis_effects.fade_out()
            if fade and stream.failure is None:
                frames = max(2, round(_STOP_FADE_SECONDS * self._sample_rate))
                gain = np.linspace(1.0, 0.0, frames)[:, None]
                # Hold only the last delivered sample while fading. Pulling
                # another block could trigger a note after transport stop.
                self._native_tail = np.rint(stream.last_frame * gain).astype('<i2')

    def _mix_native_locked(self, payload: bytes, analysis: bytes, frames: int):
        stream = self._native_stream
        tail = self._native_tail
        sample_gain = self._sample_gain.block(frames)
        native_gain = self._native_gain.block(frames)
        if stream is None and not len(tail) and np.isscalar(sample_gain) and sample_gain == 1:
            return payload, analysis
        native = np.zeros((frames, 2), dtype='<i2')
        if stream is not None and stream.failure is None:
            try:
                native[:] = stream.pull(frames)
            except Exception as error:
                stream.failure = str(error)
                logger.exception('SFZ audio block failed')
        native = scale_pcm(native, native_gain).astype(np.int32)
        count = min(frames, len(tail))
        if count:
            gain = native_gain if np.isscalar(native_gain) else native_gain[:count]
            native[:count] += scale_pcm(tail[:count], gain)
            self._native_tail = tail[count:]

        def combine(data):
            pcm = scale_pcm(np.frombuffer(data, dtype='<i2').reshape(-1, 2), sample_gain)
            return np.clip(pcm + native, -32767, 32767).astype('<i2').tobytes()

        return combine(payload), combine(analysis)

    def _read_output_frames(self, frame_count: int) -> bytes:
        return self._mix_frames(frame_count, output_delivery=True)

    def _mix_frames(self, frame_count: int, *, output_delivery: bool) -> bytes:
        try:
            if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count < 0:
                raise ValueError("frame_count must be a non-negative integer")
            with self._state_lock:
                self._drain_commands()
                if frame_count == 0:
                    return b""
                parts = []
                analysis_parts = []
                remaining = frame_count
                while remaining:
                    with self._command_lock:
                        due = []
                        while self._timed_commands and self._timed_commands[0][0] <= self._output_frame:
                            due.append(heappop(self._timed_commands))
                        next_frame = self._timed_commands[0][0] if self._timed_commands else self._output_frame + remaining
                    for target, _, command in due:
                        late = max(0, self._output_frame - target)
                        if late and isinstance(command, _SeekVoice):
                            command = _SeekVoice(command.voice_id, command.seconds + late / self._sample_rate, command.fade_in)
                        self._apply_commands((command,))
                        if late and isinstance(command, _StartVoice):
                            self._apply_commands((_SeekVoice(command.voice_id, late / self._sample_rate),))
                        elif late and isinstance(command, _ReleaseVoice):
                            for voice in self._voices:
                                if voice.voice_id == command.voice_id:
                                    voice.release_start_frame = target
                    count = min(remaining, 1024, max(1, next_frame - self._output_frame))
                    pcm = self._mix_locked(count)
                    pcm, analysis = self._mix_native_locked(pcm, self._analysis_payload, count)
                    pcm = self._sound_effects.process(pcm)
                    analysis = self._analysis_effects.process(analysis)
                    parts.append(pcm)
                    analysis_parts.append(analysis)
                    remaining -= count
                payload = b''.join(parts)
                self._analysis_payload = b''.join(analysis_parts)
                if output_delivery:
                    tap = self._visualizer_tap
                    if tap is not None:
                        tap.publish(self._analysis_payload)
                    self._emit_stop_delivery_if_ready()
                return payload
        except Exception:
            logger.exception("Failed to mix %r piano frames", frame_count)
            raise

    def release_all_and_wait_for_stop(self, timeout_ms: int = 100) -> bool:
        """Wait for STOP PCM to pass through the sink's QIODevice pull path."""
        try:
            if (
                isinstance(timeout_ms, bool)
                or not isinstance(timeout_ms, int)
                or timeout_ms <= 0
            ):
                raise ValueError("timeout_ms must be a positive integer")
            delivered = False
            loop = QEventLoop()
            timeout = QTimer()
            timeout.setSingleShot(True)

            def finish_delivery() -> None:
                nonlocal delivered
                delivered = True
                loop.quit()

            self.stopFadeDelivered.connect(finish_delivery)
            timeout.timeout.connect(loop.quit)
            try:
                self.release_all(ReleaseMode.STOP)
                timeout.start(timeout_ms)
                if not delivered:
                    loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            finally:
                timeout.stop()
                self.stopFadeDelivered.disconnect(finish_delivery)
            if not delivered:
                logger.warning(
                    "Timed out after %d ms waiting for STOP PCM delivery",
                    timeout_ms,
                )
            return delivered
        except Exception:
            logger.exception("Failed while waiting for STOP PCM delivery")
            raise

    def start_audio(
        self, *, buffer_milliseconds: int | None = AUDITION_BUFFER_MILLISECONDS
    ) -> None:
        """Start one sink lazily; construction and tests require no audio hardware."""
        try:
            if (
                buffer_milliseconds is not None
                and (
                    isinstance(buffer_milliseconds, bool)
                    or not isinstance(buffer_milliseconds, int)
                    or buffer_milliseconds <= 0
                )
            ):
                raise ValueError("buffer_milliseconds must be a positive integer")
            with self._audio_lock:
                if self._sink is not None:
                    if not self._sink_requires_restart_locked(self._sink):
                        self._refresh_audio_ready_locked(self._sink)
                        return
                    retired_sink = self._sink
                    self._sink = None
                    self._set_audio_ready_locked(False)
                    try:
                        retired_sink.stop()
                    except RuntimeError:
                        logger.warning("Invalidated piano audio sink was already deleted")
                    try:
                        retired_sink.deleteLater()
                    except RuntimeError:
                        logger.warning("Invalidated piano audio sink could not be retired")
                self._start_audio_locked(buffer_milliseconds)
        except Exception:
            logger.exception("Failed to start piano audio sink")
            raise

    def _sink_requires_restart_locked(self, sink: QAudioSink) -> bool:
        try:
            error_accessor = getattr(sink, "error", None)
            if callable(error_accessor):
                error_name = getattr(error_accessor(), "name", None)
                if error_name not in {None, "NoError", "UnderrunError"}:
                    return True
            state_accessor = getattr(sink, "state", None)
            if not callable(state_accessor):
                return False
            return getattr(state_accessor(), "name", None) == "StoppedState"
        except RuntimeError:
            return True

    def _start_audio_locked(self, buffer_milliseconds: int | None) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self._sample_rate)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        sink = ThreadedAudioSink(
            lambda parent: QAudioSink(device, audio_format, parent),
            lambda parent: PianoMixIODevice(self, parent),
            self,
        )
        try:
            if buffer_milliseconds is not None:
                sink.setBufferSize(
                    self._sample_rate * 2 * 2 * buffer_milliseconds // 1_000
                )
            if self._volume_configured:
                sink.setVolume(self._volume)
            self._sink = sink
            self._sink_frame_origin = self._output_frame
            self._buffer_milliseconds = buffer_milliseconds
            state_changed = getattr(sink, "stateChanged", None)
            if state_changed is not None and callable(getattr(state_changed, "connect", None)):
                state_changed.connect(self._on_sink_state_changed)
            sink.start(self._device)
            error = getattr(sink, "error", None)
            if callable(error):
                sink_error = error()
                error_name = getattr(sink_error, "name", None)
                if error_name not in {"NoError", "UnderrunError"}:
                    raise RuntimeError(f"Could not start audio output: {sink_error}")
            self._refresh_audio_ready_locked(sink)
        except Exception:
            self._sink = None
            self._set_audio_ready_locked(False)
            sink.deleteLater()
            raise

    def _on_sink_state_changed(self, state) -> None:
        with self._audio_lock:
            if self._sink is None or self.sender() is not self._sink:
                return
            # Windows may deliver a queued state notification from before the
            # successful start completed.  Query the sink again instead of
            # allowing that stale payload to overwrite its current readiness.
            self._refresh_audio_ready_locked(self._sink)

    def _refresh_audio_ready_locked(self, sink: QAudioSink) -> None:
        state_accessor = getattr(sink, "state", None)
        if not callable(state_accessor):
            # Lightweight test doubles and alternate Qt backends may not expose
            # state(); a successfully returned start() is their readiness edge.
            self._set_audio_ready_locked(True)
            return
        try:
            self._set_audio_ready_locked(self._sink_state_is_ready(state_accessor()))
        except RuntimeError:
            self._set_audio_ready_locked(False)

    @staticmethod
    def _sink_state_is_ready(state) -> bool:
        # In pull mode Qt legitimately reports IdleState while the sink waits
        # for the next non-silent frames.  It is still started and usable; only
        # SuspendedState and StoppedState must hold playback in preparation.
        # PySide can wrap the same C++ enum value in a distinct Python enum
        # object, so equality against QAudio.State.ActiveState is unreliable
        # on the Windows backend.  The enum name is stable across wrappers.
        return getattr(state, "name", None) in {"ActiveState", "IdleState"}

    def _set_audio_ready_locked(self, ready: bool) -> None:
        ready = bool(ready)
        if ready == self._audio_ready:
            return
        self._audio_ready = ready
        self.audioReadyChanged.emit(ready)

    def stop_audio(self) -> None:
        try:
            with self._audio_lock:
                sink = self._sink
                self._sink = None
                self._set_audio_ready_locked(False)
                if sink is None:
                    return
                try:
                    sink.stop()
                except RuntimeError:
                    logger.warning("Piano audio sink was already deleted during stop")
                    return
                try:
                    sink.deleteLater()
                except RuntimeError:
                    logger.warning("Piano audio sink was already deleted during cleanup")
        except Exception:
            logger.exception("Failed to stop piano audio sink")
            raise

    def pause_audio(self) -> None:
        with self._audio_lock:
            if self._sink is not None:
                self._sink.suspend()
            self._set_audio_ready_locked(False)

    def resume_audio(self) -> None:
        with self._audio_lock:
            if self._sink is not None:
                self._sink.resume()
                self._refresh_audio_ready_locked(self._sink)

    def set_volume(self, volume: float) -> None:
        with self._audio_lock:
            self._volume = volume
            self._volume_configured = True
            if self._sink is not None:
                self._sink.setVolume(volume)

    def _drain_commands(self) -> None:
        with self._command_lock:
            commands = tuple(self._commands)
            self._commands.clear()
        self._apply_commands(commands)

    def _apply_commands(self, commands) -> None:
        for command in commands:
            try:
                if isinstance(command, _StartVoice):
                    self._apply_start(command)
                elif isinstance(command, _ReleaseVoice):
                    self._apply_release(command)
                elif isinstance(command, _SeekVoice):
                    for voice in self._voices:
                        if voice.voice_id == command.voice_id:
                            for contribution in voice.contributions:
                                position = command.seconds * contribution.sample_rate
                                frames = len(contribution.samples) // contribution.channels
                                contribution.read_position = position % frames if contribution.looping and frames else position
                            voice.current_gain = (0.0 if command.fade_in else
                                                  min(voice.target_gain, command.seconds * self._sample_rate * voice.attack_step))
                else:
                    self._apply_release_all(command)
            except Exception:
                logger.exception("Failed to apply piano mixer command")

    def _apply_start(self, command: _StartVoice) -> None:
        replaced = [voice for voice in self._voices if voice.voice_id == command.voice_id]
        self._finish_voices(replaced)

        if len(self._voices) >= self._max_voices:
            victim = self._select_victim(command.owner_tag)
            self._steal_count += 1
            # Copy only the next 12 ms into a fixed-size tail bus. Sample
            # ownership can retire now without cutting the outgoing waveform.
            offsets = np.arange(len(self._steal_left), dtype=np.float64)
            gains = np.linspace(victim.current_gain, 0, len(offsets))
            tail_left, tail_right = ((self._monitor_steal_left, self._monitor_steal_right)
                                    if victim.owner_tag.startswith('monitor-only')
                                    else (self._steal_left, self._steal_right))
            for contribution in victim.contributions:
                count = len(offsets) if contribution.looping else _available_frames(contribution, len(offsets))
                if count:
                    _mix_contribution(contribution, offsets[:count], gains[:count], tail_left[:count], tail_right[:count])
            self._finish_voices((victim,))

        target_gain = command.velocity / 127.0
        attack_frames = max(1, round(self._profile.attack_seconds(command.velocity) * self._sample_rate))
        self._voices.append(
            PCMVoice(
                voice_id=command.voice_id,
                pitch=command.pitch,
                velocity=command.velocity,
                contributions=tuple(
                    _PCMContributionState(
                        item.samples,
                        item.sample_rate,
                        item.channels,
                        item.gain,
                        item.pan,
                        item.looping,
                        sample_values=np.asarray(item.samples, dtype=np.int16),
                        source_step=item.sample_rate / self._sample_rate,
                        left_pan_gain=cos((item.pan + 1.0) * pi / 4.0),
                        right_pan_gain=sin((item.pan + 1.0) * pi / 4.0),
                    )
                    for item in command.contributions
                ),
                current_gain=0.0,
                target_gain=target_gain,
                envelope_mode=None,
                release_start=None,
                release_start_frame=None,
                release_seconds=0.0,
                release_start_gain=0.0,
                age=command.queued_at,
                owner_tag=command.owner_tag,
                attack_step=target_gain / attack_frames,
            )
        )
        self._peak_voices = max(self._peak_voices, len(self._voices))

    def _apply_release(self, command: _ReleaseVoice) -> None:
        for voice in self._voices:
            if voice.voice_id != command.voice_id:
                continue
            self._begin_release(
                voice,
                command.mode,
                command.queued_at,
                command.release_seconds,
            )
            return

    def _apply_release_all(self, command: _ReleaseAll) -> None:
        with self._command_lock:
            cancelled = [item[2].voice_id for item in self._timed_commands if isinstance(item[2], _StartVoice)]
            self._timed_commands.clear()
        for voice_id in cancelled:
            self.voiceFinished.emit(voice_id)
        had_voices = bool(self._voices)
        for voice in self._voices:
            self._begin_release(voice, command.mode, command.queued_at)
        if command.mode is ReleaseMode.STOP:
            self._sound_effects.fade_out()
            self._analysis_effects.fade_out()
            stop_frames = max(1, round(_STOP_FADE_SECONDS * self._sample_rate))
            self._stop_delivery_frame = self._output_frame + (
                stop_frames if had_voices else 0
            ) + 1

    def _emit_stop_delivery_if_ready(self) -> None:
        target = self._stop_delivery_frame
        if target is None or self._output_frame < target:
            return
        self._stop_delivery_frame = None
        self.stopFadeDelivered.emit()

    def _begin_release(
        self,
        voice: PCMVoice,
        mode: ReleaseMode,
        queued_at: float,
        release_seconds: float | None = None,
    ) -> None:
        if voice.envelope_mode is not None and mode is not ReleaseMode.STOP:
            return
        if voice.envelope_mode is ReleaseMode.STOP:
            return
        energy = min(1.0, max(0.0, voice.current_gain))
        if mode is ReleaseMode.STOP:
            release_seconds = _STOP_FADE_SECONDS
        elif mode is ReleaseMode.NATURAL and release_seconds is not None:
            release_seconds = _validate_release_seconds(release_seconds)
        else:
            release_seconds = self._profile.release_seconds(
                voice.pitch,
                voice.velocity,
                energy,
                mode,
            )
        voice.envelope_mode = mode
        voice.release_start = queued_at
        voice.release_start_frame = self._output_frame
        voice.release_seconds = release_seconds
        voice.release_start_gain = voice.current_gain
        voice.target_gain = 0.0

    def _select_victim(self, current_owner_tag: str) -> PCMVoice:
        releasing = [voice for voice in self._voices if voice.envelope_mode is not None]
        quiet_releasing = [voice for voice in releasing if voice.current_gain <= _QUIET_GAIN]
        if quiet_releasing:
            return min(quiet_releasing, key=lambda voice: (voice.current_gain, voice.age))
        if releasing:
            return min(
                releasing,
                key=lambda voice: (
                    voice.release_start if voice.release_start is not None else voice.age,
                    voice.age,
                ),
            )
        quiet_non_current = [
            voice
            for voice in self._voices
            if voice.owner_tag != current_owner_tag and voice.current_gain <= _QUIET_GAIN
        ]
        if quiet_non_current:
            return min(quiet_non_current, key=lambda voice: (voice.current_gain, voice.age))
        return min(self._voices, key=lambda voice: voice.age)

    def _mix_locked(self, frame_count: int) -> bytes:
        frame_offsets = np.arange(frame_count, dtype=np.float64)
        left_mix = np.zeros(frame_count, dtype=np.float64)
        right_mix = np.zeros(frame_count, dtype=np.float64)
        monitor_left = np.zeros(frame_count, dtype=np.float64)
        monitor_right = np.zeros(frame_count, dtype=np.float64)
        tail_frames = min(frame_count, len(self._steal_left))
        left_mix[:tail_frames] += self._steal_left[:tail_frames]
        right_mix[:tail_frames] += self._steal_right[:tail_frames]
        monitor_left[:tail_frames] += self._monitor_steal_left[:tail_frames]
        monitor_right[:tail_frames] += self._monitor_steal_right[:tail_frames]
        for tail in (self._steal_left, self._steal_right, self._monitor_steal_left, self._monitor_steal_right):
            tail[:-tail_frames] = tail[tail_frames:]
            tail[-tail_frames:] = 0
        finished: list[PCMVoice] = []

        for voice in self._voices:
            active_frames = max(
                (
                    frame_count
                    if contribution.looping
                    else _available_frames(contribution, frame_count)
                    for contribution in voice.contributions
                ),
                default=0,
            )
            if not active_frames:
                finished.append(voice)
                continue

            release_frames = 0
            if voice.envelope_mode is not None:
                release_frames = max(1, round(voice.release_seconds * self._sample_rate))
                assert voice.release_start_frame is not None
                active_frames = min(
                    active_frames,
                    release_frames - (self._output_frame - voice.release_start_frame),
                )
            if active_frames <= 0:
                finished.append(voice)
                continue

            gains = _voice_gains(
                voice,
                active_frames,
                self._output_frame,
                self._profile.release_curve,
                release_frames,
            )
            voice.current_gain = float(gains[-1])
            for contribution in voice.contributions:
                contribution_frames = (
                    active_frames
                    if contribution.looping
                    else min(active_frames, _available_frames(contribution, active_frames))
                )
                if not contribution_frames:
                    continue
                _mix_contribution(
                    contribution,
                    frame_offsets[:contribution_frames],
                    gains[:contribution_frames],
                    (monitor_left if voice.owner_tag == 'monitor-only' else left_mix)[:contribution_frames],
                    (monitor_right if voice.owner_tag == 'monitor-only' else right_mix)[:contribution_frames],
                )
                contribution.read_position += contribution.source_step * contribution_frames

            if voice.envelope_mode is not None and (
                self._output_frame + active_frames - voice.release_start_frame
                >= release_frames
            ):
                finished.append(voice)
            elif active_frames < frame_count:
                finished.append(voice)

        self._output_frame += frame_count
        if finished:
            self._finish_voices(finished)
        # Samples are intentionally boosted before the bounded soft limiter.
        # QAudioSink's 0..1 master slider then remains a predictable user
        # control while quiet piano recordings have the requested headroom.
        self._analysis_payload = _stereo_pcm(left_mix * MASTER_OUTPUT_GAIN, right_mix * MASTER_OUTPUT_GAIN, self._profile.peak_amplitude)
        return _stereo_pcm(
            (left_mix + monitor_left) * MASTER_OUTPUT_GAIN,
            (right_mix + monitor_right) * MASTER_OUTPUT_GAIN,
            self._profile.peak_amplitude,
        )

    def _finish_voices(self, voices: Sequence[PCMVoice]) -> None:
        finished_ids = {id(voice) for voice in voices}
        if not finished_ids:
            return
        removed = tuple(voice for voice in self._voices if id(voice) in finished_ids)
        self._voices = [voice for voice in self._voices if id(voice) not in finished_ids]
        removed_ids = {voice.voice_id for voice in removed}
        with self._command_lock:
            self._timed_commands = [item for item in self._timed_commands
                                    if not isinstance(item[2], (_ReleaseVoice, _SeekVoice))
                                    or item[2].voice_id not in removed_ids]
            heapify(self._timed_commands)
        for voice in removed:
            self.voiceFinished.emit(voice.voice_id)


def _available_frames(contribution: _PCMContributionState, requested_frames: int) -> int:
    source_frames = len(contribution.samples) // contribution.channels
    if contribution.read_position >= source_frames:
        return 0
    return min(
        requested_frames,
        int(np.ceil((source_frames - contribution.read_position) / contribution.source_step)),
    )


def _voice_gains(
    voice: PCMVoice,
    frame_count: int,
    output_frame: int,
    release_curve: float,
    release_frames: int,
) -> np.ndarray:
    if voice.envelope_mode is None:
        gains = voice.current_gain + voice.attack_step * np.arange(
            1, frame_count + 1, dtype=np.float64
        )
        return np.minimum(gains, voice.target_gain)

    assert voice.release_start_frame is not None
    progress = np.minimum(
        1.0,
        (output_frame - voice.release_start_frame + np.arange(frame_count))
        / release_frames,
    )
    return voice.release_start_gain * np.power(1.0 - progress, release_curve)


def _mix_contribution(
    contribution: _PCMContributionState,
    frame_offsets: np.ndarray,
    gains: np.ndarray,
    left_mix: np.ndarray,
    right_mix: np.ndarray,
) -> None:
    samples = contribution.sample_values
    assert samples is not None
    source_frames = len(samples) // contribution.channels
    positions = contribution.read_position + contribution.source_step * frame_offsets
    if contribution.looping:
        positions = np.remainder(positions, source_frames)
    lower = positions.astype(np.intp)
    upper = (
        (lower + 1) % source_frames
        if contribution.looping
        else np.minimum(lower + 1, source_frames - 1)
    )
    fraction = positions - lower
    lower_offset = lower * contribution.channels
    upper_offset = upper * contribution.channels
    lower_left = samples[lower_offset].astype(np.float64)
    upper_left = samples[upper_offset].astype(np.float64)
    left_samples = lower_left + (upper_left - lower_left) * fraction
    if contribution.channels == 1:
        right_samples = left_samples
    else:
        left_channel = lower_offset + 1
        right_channel = upper_offset + 1
        lower_right = samples[left_channel].astype(np.float64)
        upper_right = samples[right_channel].astype(np.float64)
        right_samples = lower_right + (upper_right - lower_right) * fraction
    contribution_gain = gains * contribution.gain / _INT16_SCALE
    left_mix += left_samples * contribution_gain * contribution.left_pan_gain
    right_mix += right_samples * contribution_gain * contribution.right_pan_gain


def _stereo_pcm(left_mix: np.ndarray, right_mix: np.ndarray, peak_amplitude: float) -> bytes:
    limit = peak_amplitude
    knee = limit * 0.85
    headroom = limit - knee

    def soft_limit(values: np.ndarray) -> np.ndarray:
        magnitude = np.abs(values)
        limited = knee + headroom * (1.0 - np.exp(-(magnitude - knee) / headroom))
        return np.where(magnitude <= knee, values, np.copysign(limited, values))

    peak = int(_INT16_SCALE * peak_amplitude)
    output = np.empty((len(left_mix), 2), dtype="<i2")
    output[:, 0] = np.clip(np.rint(soft_limit(left_mix) * _INT16_SCALE), -peak, peak)
    output[:, 1] = np.clip(np.rint(soft_limit(right_mix) * _INT16_SCALE), -peak, peak)
    return output.tobytes()


def _immutable_pcm(source: Sequence[int]) -> memoryview | tuple[int, ...]:
    if isinstance(source, memoryview):
        if (
            source.readonly
            and source.ndim == 1
            and source.format in ("h", "<h", "=h")
            and len(source) > 0
        ):
            return source
        raise ValueError("source memoryview must expose immutable signed 16-bit samples")
    if isinstance(source, (str, bytes, bytearray)):
        raise ValueError("source must be a non-empty sequence of signed 16-bit samples")
    result = tuple(source)
    if not result:
        raise ValueError("source must be a non-empty sequence of signed 16-bit samples")
    if any(
        isinstance(sample, bool)
        or not isinstance(sample, int)
        or not -32_768 <= sample <= 32_767
        for sample in result
    ):
        raise ValueError("source samples must be signed 16-bit integers")
    return result


def _validate_contribution(contribution: PCMContribution) -> PCMContribution:
    if not isinstance(contribution, PCMContribution):
        raise ValueError("contributions must contain PCMContribution values")
    samples = contribution.samples
    if not isinstance(contribution.looping, bool):
        raise ValueError("contribution looping must be a bool")
    if isinstance(samples, memoryview):
        if not samples.readonly:
            raise ValueError("contribution memoryviews must be read-only")
        if samples.ndim != 1 or samples.format not in ("h", "<h", "=h"):
            raise ValueError("contribution memoryviews must expose signed 16-bit samples")
        if not samples:
            raise ValueError("contribution samples must not be empty")
    elif isinstance(samples, tuple):
        if not samples or any(
            isinstance(sample, bool)
            or not isinstance(sample, int)
            or not -32_768 <= sample <= 32_767
            for sample in samples
        ):
            raise ValueError("contribution samples must be signed 16-bit integers")
    else:
        raise ValueError("contribution samples must be an immutable buffer or tuple")
    sample_rate = _validate_sample_rate(contribution.sample_rate)
    if contribution.channels not in (1, 2):
        raise ValueError("contribution channels must be 1 or 2")
    if len(samples) % contribution.channels:
        raise ValueError("contribution samples must contain complete frames")
    gain = _validate_gain(contribution.gain)
    pan = _validate_pan(contribution.pan)
    return PCMContribution(
        samples, sample_rate, contribution.channels, gain, pan, contribution.looping
    )


def _validate_voice_id(voice_id: str) -> str:
    if not isinstance(voice_id, str) or not voice_id:
        raise ValueError("voice_id must be a non-empty string")
    return voice_id


def _validate_owner_tag(owner_tag: str) -> str:
    if not isinstance(owner_tag, str) or not owner_tag:
        raise ValueError("owner_tag must be a non-empty string")
    return owner_tag


def _validate_midi_value(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 127:
        raise ValueError(f"{name} must be an integer from 0 to 127")
    return value


def _validate_sample_rate(sample_rate: int) -> int:
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("source_sample_rate must be a positive integer")
    return sample_rate


def _validate_pan(pan: float) -> float:
    if isinstance(pan, bool) or not isinstance(pan, (int, float)) or not isfinite(pan):
        raise ValueError("pan must be a finite number from -1 to 1")
    if not -1.0 <= pan <= 1.0:
        raise ValueError("pan must be a finite number from -1 to 1")
    return float(pan)


def _validate_gain(gain: float) -> float:
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not isfinite(gain):
        raise ValueError("gain must be a finite non-negative number")
    if gain < 0.0:
        raise ValueError("gain must be a finite non-negative number")
    return float(gain)


def _validate_release_seconds(release_seconds: float | None) -> float | None:
    if release_seconds is None:
        return None
    if (
        isinstance(release_seconds, bool)
        or not isinstance(release_seconds, (int, float))
        or not isfinite(float(release_seconds))
        or release_seconds < 0.0
        or release_seconds > _MAX_RELEASE_SECONDS
    ):
        raise ValueError(
            f"release_seconds must be a finite number from 0 to {_MAX_RELEASE_SECONDS:g}"
        )
    return float(release_seconds)


def _interpolated_frame(
    source: memoryview | tuple[int, ...],
    position: float,
    channels: int,
    *,
    looping: bool = False,
) -> tuple[float, float]:
    frame_count = len(source) // channels
    lower = min(int(position), frame_count - 1)
    upper = (lower + 1) % frame_count if looping else min(lower + 1, frame_count - 1)
    fraction = position - int(position)

    def channel_value(channel: int) -> float:
        lower_value = source[lower * channels + channel]
        upper_value = source[upper * channels + channel]
        return lower_value + (upper_value - lower_value) * fraction

    left = channel_value(0)
    return (left, left) if channels == 1 else (left, channel_value(1))


def _soft_limit(value: float, limit: float) -> float:
    knee = limit * 0.85
    magnitude = abs(value)
    if magnitude <= knee:
        return value
    headroom = limit - knee
    limited = knee + headroom * (1.0 - exp(-(magnitude - knee) / headroom))
    return limited if value >= 0.0 else -limited


def _to_int16(value: float, profile: PianoVoiceProfile) -> int:
    peak = int(_INT16_SCALE * profile.peak_amplitude)
    return min(peak, max(-peak, round(value * _INT16_SCALE)))
