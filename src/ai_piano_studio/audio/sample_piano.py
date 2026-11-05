from __future__ import annotations

from array import array
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import logging
from math import cos, pi, sin, sqrt
from pathlib import Path
import sys
from threading import RLock
from time import perf_counter
from typing import Callable, Iterable, Sequence
import wave

from ai_piano_studio.audio.profile import (
    ACOUSTIC_PIANO_V2,
    SAMPLE_PIANO_V1,
    ReleaseMode,
)
from ai_piano_studio.audio.synth import SAMPLE_RATE, render_note as render_legacy_note
from ai_piano_studio.audio.synth import render_project as render_legacy_project
from ai_piano_studio.domain.control_event import sounding_duration
from ai_piano_studio.audio.synth import note_release_override
from ai_piano_studio.domain.note import Note


logger = logging.getLogger(__name__)

SAMPLE_ROOT_MIDI = (
    21,
    24,
    27,
    33,
    39,
    45,
    51,
    57,
    63,
    69,
    66,
    75,
    81,
    87,
    93,
    99,
    105,
    108,
)
_SAMPLE_ROOT_NOTES = (
    "A0",
    "C1",
    "D#1",
    "A1",
    "D#2",
    "A2",
    "D#3",
    "A3",
    "D#4",
    "A4",
    "F#4",
    "D#5",
    "A5",
    "D#6",
    "A6",
    "D#7",
    "A7",
    "C8",
)


@dataclass(frozen=True, slots=True)
class DecodedSample:
    samples: memoryview
    sample_rate: int
    channels: int
    frame_count: int

    @property
    def byte_count(self) -> int:
        return self.samples.nbytes


@dataclass(frozen=True, slots=True)
class SampleVoice:
    token: int
    pitch: int
    velocity: int
    root_midi: int
    pitch_ratio: float
    soft_gain: float
    hard_gain: float
    velocity_gain: float
    pan: float
    soft_buffer: DecodedSample
    hard_buffer: DecodedSample


@dataclass(frozen=True, slots=True)
class LegacyVoice:
    pitch: int
    velocity: int
    reason: str
    source: tuple[int, ...] | memoryview | None = None
    looping: bool = False
    sample_rate: int = SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class _LayerMetadata:
    path: Path
    frame_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _RootMetadata:
    root_midi: int
    root_note: str
    soft: _LayerMetadata
    hard: _LayerMetadata


@dataclass(slots=True)
class _CacheEntry:
    soft: DecodedSample
    hard: DecodedSample
    reference_count: int = 0

    @property
    def byte_count(self) -> int:
        return self.soft.byte_count + self.hard.byte_count


Decoder = Callable[..., DecodedSample]
Scheduler = Callable[[Callable[[], None]], None]


def decode_wav(
    path: Path,
    *,
    sample_rate: int,
    channels: int,
    frame_count: int,
) -> DecodedSample:
    """Decode one validated signed-16 PCM WAV into immutable interleaved PCM."""
    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is unsupported: {path.name}")
        if source.getsampwidth() != 2:
            raise ValueError(f"sample width mismatch: {path.name}")
        if source.getframerate() != sample_rate:
            raise ValueError(f"sample rate mismatch: {path.name}")
        if source.getnchannels() != channels:
            raise ValueError(f"channel count mismatch: {path.name}")
        if source.getnframes() != frame_count:
            raise ValueError(f"frame count mismatch: {path.name}")
        payload = source.readframes(frame_count)
    pcm = array("h")
    pcm.frombytes(payload)
    if sys.byteorder != "little":
        pcm.byteswap()
    if len(pcm) != frame_count * channels:
        raise ValueError(f"decoded PCM length mismatch: {path.name}")
    samples = memoryview(pcm.tobytes()).cast("h").toreadonly()
    return DecodedSample(samples, sample_rate, channels, frame_count)


class SamplePianoEngine:
    """Qt-free sample selection, asynchronous warm-up, and decoded root cache."""

    def __init__(
        self,
        manifest_path: Path | str,
        *,
        cache_byte_limit: int = SAMPLE_PIANO_V1.cache_byte_limit,
        decoder: Decoder = decode_wav,
        scheduler: Scheduler | None = None,
        preload_c3_c5_on_init: bool = True,
    ) -> None:
        self._lock = RLock()
        self._manifest_path = Path(manifest_path)
        self._cache_byte_limit = cache_byte_limit
        self._decoder = decoder
        self._scheduler = scheduler or _schedule_background
        self._roots: dict[int, _RootMetadata] = {}
        self._root_order: tuple[int, ...] = ()
        self._root_notes: tuple[str, ...] = ()
        self._sample_rate = 32_000
        self._channels = 2
        self._cache: OrderedDict[int, _CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._warmup_generation = 0
        self._warmup_active = True
        self._discard_idle_on_release = False
        self._scheduled_roots: set[int] = set()
        self._active_tokens: dict[int, int] = {}
        self._next_token = 1
        self._hits = 0
        self._misses = 0
        self._load_count = 0
        self._decode_seconds = 0.0
        self._legacy_selected = False
        self._failure_reason = ""
        try:
            if (
                isinstance(cache_byte_limit, bool)
                or not isinstance(cache_byte_limit, int)
                or cache_byte_limit <= 0
            ):
                raise ValueError("cache_byte_limit must be a positive integer")
            if not callable(decoder):
                raise ValueError("decoder must be callable")
            if not callable(self._scheduler):
                raise ValueError("scheduler must be callable")
            if not isinstance(preload_c3_c5_on_init, bool):
                raise ValueError("preload_c3_c5_on_init must be a bool")
            self._read_manifest_metadata()
            if preload_c3_c5_on_init:
                self.preload_c3_c5_async()
        except Exception as error:
            self._select_legacy(f"manifest validation failed: {error}")

    @property
    def legacy_selected(self) -> bool:
        with self._lock:
            return self._legacy_selected

    @property
    def failure_reason(self) -> str:
        with self._lock:
            return self._failure_reason

    @property
    def root_notes(self) -> tuple[str, ...]:
        return self._root_notes

    @property
    def c3_c5_root_midis(self) -> tuple[int, ...]:
        selected = {
            self.nearest_root(pitch)
            for pitch in range(
                SAMPLE_PIANO_V1.preload_low_midi,
                SAMPLE_PIANO_V1.preload_high_midi + 1,
            )
        }
        return tuple(root for root in sorted(self._root_order) if root in selected)

    @property
    def cache_bytes(self) -> int:
        with self._lock:
            return self._cache_bytes

    @property
    def cached_root_midis(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._cache)

    def nearest_root(self, pitch: int) -> int:
        _validate_pitch(pitch)
        if not self._root_order:
            raise ValueError("sample root map is unavailable")
        return min(self._root_order, key=lambda root: abs(pitch - root))

    @staticmethod
    def velocity_layer_gains(velocity: int) -> tuple[float, float]:
        _validate_velocity(velocity)
        blend = (velocity - 1) / 126
        return cos(blend * pi / 2), sin(blend * pi / 2)

    @staticmethod
    def pitch_ratio(pitch: int, root_midi: int) -> float:
        _validate_pitch(pitch)
        _validate_pitch(root_midi)
        return 2 ** ((pitch - root_midi) / 12)

    @staticmethod
    def pan_for_pitch(pitch: int) -> float:
        _validate_pitch(pitch)
        return SAMPLE_PIANO_V1.max_pan * (pitch - 21) / (108 - 21) * 2 - SAMPLE_PIANO_V1.max_pan

    def preload_c3_c5_sync(self) -> bool:
        if self.legacy_selected:
            return False
        return self.preload_roots_sync(self.c3_c5_root_midis)

    def preload_c3_c5_async(self) -> None:
        if self.legacy_selected:
            return
        self._schedule_roots(self.c3_c5_root_midis)

    def prefetch_pitches(self, pitches: Iterable[int]) -> None:
        """Bounded metadata-only requests; decoding stays on the load pool."""
        with self._lock:
            if self._legacy_selected:
                return
        roots = dict.fromkeys(self.nearest_root(pitch) for pitch in pitches
                              if isinstance(pitch, int) and not isinstance(pitch, bool) and 21 <= pitch <= 108)
        self._schedule_roots(roots)

    def ready_for_pitches(self, pitches: Iterable[int]) -> bool:
        with self._lock:
            if self._legacy_selected:
                return True
            roots = {self.nearest_root(pitch) for pitch in pitches if 21 <= pitch <= 108}
            return self._legacy_selected or roots.issubset(self._cache)

    def preload_remaining_async(self) -> None:
        # Bundled banks fit the existing budget. Larger custom banks retain
        # context-only prefetch instead of churning the cache at startup.
        required = sum((root.soft.frame_count + root.hard.frame_count)
                       * self._channels * 2 for root in self._roots.values())
        if required <= self._cache_byte_limit:
            self._schedule_roots(sorted(self._root_order, key=lambda pitch: abs(pitch - 60)))

    def cache_metrics(self) -> dict:
        with self._lock:
            return dict(hits=self._hits, misses=self._misses, loads=self._load_count,
                        decode_ms=self._decode_seconds * 1000, pending=len(self._scheduled_roots),
                        cached_bytes=self._cache_bytes, capacity_bytes=self._cache_byte_limit)

    def discard_idle_cache(self) -> None:
        """Release decoded roots not retained by an audible voice."""
        with self._lock:
            if not self._warmup_active:
                return
            self._warmup_generation += 1
            self._warmup_active = False
            self._discard_idle_on_release = True
            stale = tuple(
                root_midi
                for root_midi, entry in self._cache.items()
                if entry.reference_count == 0
            )
            for root_midi in stale:
                entry = self._cache.pop(root_midi)
                self._cache_bytes -= entry.byte_count

    def activate_cache(self) -> None:
        with self._lock:
            if self._warmup_active:
                return
            self._warmup_generation += 1
            self._warmup_active = True
            self._discard_idle_on_release = False

    def preload_roots_sync(self, root_midis: Iterable[int]) -> bool:
        try:
            for root_midi in tuple(root_midis):
                if self.legacy_selected or not self._load_root(root_midi):
                    return False
            return not self.legacy_selected
        except Exception as error:
            self._select_legacy(f"sample preload failed: {error}")
            return False

    def note_on(self, pitch: int, velocity: int) -> SampleVoice | LegacyVoice:
        try:
            _validate_pitch(pitch)
            _validate_velocity(velocity)
            root_midi = self.nearest_root(pitch) if self._root_order else 0
            with self._lock:
                if self._legacy_selected:
                    return LegacyVoice(pitch, velocity, self._failure_reason)
                entry = self._cache.get(root_midi)
                if entry is not None:
                    self._hits += 1
                    entry.reference_count += 1
                    self._cache.move_to_end(root_midi)
                    token = self._next_token
                    self._next_token += 1
                    self._active_tokens[token] = root_midi
                    soft_gain, hard_gain = self.velocity_layer_gains(velocity)
                    return SampleVoice(
                        token=token,
                        pitch=pitch,
                        velocity=velocity,
                        root_midi=root_midi,
                        pitch_ratio=self.pitch_ratio(pitch, root_midi),
                        soft_gain=soft_gain,
                        hard_gain=hard_gain,
                        velocity_gain=velocity / 127,
                        pan=self.pan_for_pitch(pitch),
                        soft_buffer=entry.soft,
                        hard_buffer=entry.hard,
                    )
                self._misses += 1
            self._schedule_roots((root_midi,))
            logger.warning(
                "Sample root %s cache miss; using one Legacy Piano voice for this event",
                root_midi,
            )
            return LegacyVoice(pitch, velocity, f"root {root_midi} is warming")
        except Exception:
            logger.exception(
                "Failed to select Sample Piano note pitch=%r velocity=%r",
                pitch,
                velocity,
            )
            raise

    def release_voice(self, voice: SampleVoice | LegacyVoice) -> None:
        if not isinstance(voice, SampleVoice):
            return
        with self._lock:
            root_midi = self._active_tokens.pop(voice.token, None)
            if root_midi is None:
                return
            entry = self._cache.get(root_midi)
            if entry is not None:
                entry.reference_count = max(0, entry.reference_count - 1)
                if self._discard_idle_on_release and entry.reference_count == 0:
                    self._cache.pop(root_midi)
                    self._cache_bytes -= entry.byte_count
            self._evict_locked()

    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
        *,
        should_cancel: Callable[[], bool] | None = None,
        release_seconds: float | None = None,
    ) -> array:
        """Render a sample voice offline; synchronous loading is allowed off the input path."""
        if type(pitch) is int and 0 <= pitch <= 127 and not 21 <= pitch <= 108:
            fallback = render_legacy_note(pitch, duration, velocity, sample_rate,
                should_cancel=should_cancel, release_seconds=release_seconds)
            if self.legacy_selected:
                return fallback
            # Preserve the sample renderer's stereo contract for valid MIDI
            # notes outside the acoustic bank, without clamping their pitch.
            return array('h', (int(sample / sqrt(2)) for sample in fallback for _ in range(2)))
        _validate_pitch(pitch)
        _validate_velocity(velocity)
        if duration <= 0:
            raise ValueError("duration must be greater than zero")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")
        if should_cancel is not None and should_cancel():
            return array("h")
        root_midi = self.nearest_root(pitch) if self._root_order else 0
        if self.legacy_selected or not self.preload_roots_sync((root_midi,)):
            return render_legacy_note(
                pitch, duration, velocity, sample_rate, should_cancel=should_cancel, release_seconds=release_seconds
            )
        voice = self.note_on(pitch, velocity)
        if not isinstance(voice, SampleVoice):
            return render_legacy_note(
                pitch, duration, velocity, sample_rate, should_cancel=should_cancel, release_seconds=release_seconds
            )
        try:
            return _render_sample_voice(
                voice, duration, sample_rate, should_cancel=should_cancel, release_seconds=release_seconds
            )
        finally:
            self.release_voice(voice)

    def render_project(
        self,
        notes: Sequence[Note] | Iterable[Note],
        sample_rate: int = SAMPLE_RATE,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> array:
        notes = tuple(notes)
        if should_cancel is not None and should_cancel():
            return array("h")
        if self.legacy_selected:
            return render_legacy_project(
                notes, sample_rate, should_cancel=should_cancel
            )
        if not notes:
            return array("h")
        rendered: list[array] = []
        for note in notes:
            if should_cancel is not None and should_cancel():
                return array("h")
            rendered.append(
                self.render_note(
                    note.pitch,
                    sounding_duration(note),
                    note.velocity,
                    sample_rate,
                    should_cancel=should_cancel,
                    **({'release_seconds': note_release_override(note)} if note_release_override(note) is not None else {}),
                )
            )
        if self.legacy_selected or any(len(samples) % 2 for samples in rendered):
            return render_legacy_project(
                notes, sample_rate, should_cancel=should_cancel
            )
        total_frames = max(
            int(note.start_time * sample_rate) + len(samples) // 2
            for note, samples in zip(notes, rendered)
        )
        mix = [0.0] * (total_frames * 2)
        for note, samples in zip(notes, rendered):
            offset = int(note.start_time * sample_rate) * 2
            for index, sample in enumerate(samples):
                if (
                    should_cancel is not None
                    and index % 4096 == 0
                    and should_cancel()
                ):
                    return array("h")
                mix[offset + index] += sample
        peak = 0.0
        for index, value in enumerate(mix):
            if (
                should_cancel is not None
                and index % 4096 == 0
                and should_cancel()
            ):
                return array("h")
            peak = max(peak, abs(value))
        limit = 32_767 * ACOUSTIC_PIANO_V2.peak_amplitude
        scale = limit / peak if peak > limit else 1.0
        result = array("h")
        for index, value in enumerate(mix):
            if (
                should_cancel is not None
                and index % 4096 == 0
                and should_cancel()
            ):
                return array("h")
            result.append(_clamp_int16(value * scale))
        return result

    def _read_manifest_metadata(self) -> None:
        payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        _required_string(payload, "asset_version")
        self._sample_rate = _required_positive_int(payload, "sample_rate")
        self._channels = _required_positive_int(payload, "channels")
        if self._channels != 2:
            raise ValueError("manifest channels must be stereo (2)")
        if _required_positive_int(payload, "sample_width_bytes") != 2:
            raise ValueError("manifest sample_width_bytes must be 2")
        sustain = _required_mapping(payload, "sustain")
        if sustain.get("loop_free") is not True:
            raise ValueError("manifest sustain must be loop-free")
        crop = _required_mapping(payload, "crop")
        if not isinstance(crop.get("source_start_frame"), int) or crop["source_start_frame"] < 0:
            raise ValueError("manifest crop source_start_frame must be non-negative")
        if not isinstance(crop.get("tail_fade_ms"), int) or crop["tail_fade_ms"] < 0:
            raise ValueError("manifest crop tail_fade_ms must be non-negative")
        root_payloads = payload.get("roots")
        if not isinstance(root_payloads, list) or not root_payloads:
            raise ValueError("manifest roots must be a non-empty list")

        root_order: list[int] = []
        root_notes: list[str] = []
        roots: dict[int, _RootMetadata] = {}
        for root_payload in root_payloads:
            if not isinstance(root_payload, dict):
                raise ValueError("each manifest root must be an object")
            root_midi = _required_int(root_payload, "root_midi")
            root_note = _required_string(root_payload, "root_note")
            if root_midi in roots:
                raise ValueError(f"duplicate root MIDI {root_midi}")
            layers = _required_mapping(root_payload, "layers")
            root = _RootMetadata(
                root_midi=root_midi,
                root_note=root_note,
                soft=self._read_layer(layers, "Soft"),
                hard=self._read_layer(layers, "Hard"),
            )
            root_order.append(root_midi)
            root_notes.append(root_note)
            roots[root_midi] = root

        maximum_distance = max(
            min(abs(pitch - root) for root in root_order) for pitch in range(21, 109)
        )
        if maximum_distance > SAMPLE_PIANO_V1.max_root_distance:
            raise ValueError("nearest-root distance exceeds three semitones")
        if tuple(root_order) != SAMPLE_ROOT_MIDI or tuple(root_notes) != _SAMPLE_ROOT_NOTES:
            raise ValueError("manifest does not use the exact Sample Piano v1 root map")
        self._root_order = tuple(root_order)
        self._root_notes = tuple(root_notes)
        self._roots = roots

    def _read_layer(self, layers: dict, name: str) -> _LayerMetadata:
        layer = layers.get(name)
        if not isinstance(layer, dict):
            raise ValueError(f"root layer {name} is missing")
        filename = _required_string(layer, "file")
        relative_path = Path(filename)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"root layer {name} file must be a safe relative path")
        checksum = _required_string(layer, "sha256").lower()
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError(f"root layer {name} SHA-256 is invalid")
        return _LayerMetadata(
            path=self._manifest_path.parent / relative_path,
            frame_count=_required_positive_int(layer, "frame_count"),
            sha256=checksum,
        )

    def _schedule_roots(self, root_midis: Iterable[int]) -> None:
        for root_midi in root_midis:
            with self._lock:
                if (
                    self._legacy_selected
                    or not self._warmup_active
                    or root_midi in self._cache
                    or root_midi in self._scheduled_roots
                ):
                    continue
                self._scheduled_roots.add(root_midi)
                generation = self._warmup_generation

            def load(
                requested_root: int = root_midi, scheduled_generation: int = generation
            ) -> None:
                try:
                    self._load_root(requested_root, scheduled_generation)
                finally:
                    with self._lock:
                        self._scheduled_roots.discard(requested_root)
                        requeue = (
                            not self._legacy_selected
                            and self._warmup_active
                            and scheduled_generation != self._warmup_generation
                            and requested_root not in self._cache
                        )
                    if requeue:
                        self._schedule_roots((requested_root,))

            try:
                self._scheduler(load)
            except Exception as error:
                with self._lock:
                    self._scheduled_roots.discard(root_midi)
                self._select_legacy(f"sample preload scheduling failed: {error}")

    def _load_root(self, root_midi: int, generation: int | None = None) -> bool:
        with self._lock:
            if self._legacy_selected or (
                generation is not None
                and (
                    not self._warmup_active
                    or generation != self._warmup_generation
                )
            ):
                return False
            if root_midi in self._cache:
                self._cache.move_to_end(root_midi)
                return True
            metadata = self._roots.get(root_midi)
        if metadata is None:
            self._select_legacy(f"manifest has no metadata for root MIDI {root_midi}")
            return False
        try:
            started = perf_counter()
            soft = self._load_layer(metadata.soft)
            with self._lock:
                if generation is not None and generation != self._warmup_generation:
                    return False
            hard = self._load_layer(metadata.hard)
        except Exception as error:
            with self._lock:
                stale = generation is not None and (
                    not self._warmup_active
                    or generation != self._warmup_generation
                )
            if stale:
                return False
            self._select_legacy(f"root {metadata.root_note} load failed: {error}")
            return False

        with self._lock:
            self._load_count += 1
            self._decode_seconds += perf_counter() - started
            if self._legacy_selected or (
                generation is not None
                and (
                    not self._warmup_active
                    or generation != self._warmup_generation
                )
            ):
                return False
            if root_midi not in self._cache:
                entry = _CacheEntry(soft=soft, hard=hard)
                self._cache[root_midi] = entry
                self._cache_bytes += entry.byte_count
                self._evict_locked()
            return True

    def _load_layer(self, metadata: _LayerMetadata) -> DecodedSample:
        with metadata.path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != metadata.sha256:
            raise ValueError(f"checksum mismatch: {metadata.path.name}")
        return self._decoder(
            metadata.path,
            sample_rate=self._sample_rate,
            channels=self._channels,
            frame_count=metadata.frame_count,
        )

    def _evict_locked(self) -> None:
        while self._cache_bytes > self._cache_byte_limit:
            victim = next(
                (
                    root_midi
                    for root_midi, entry in self._cache.items()
                    if entry.reference_count == 0
                ),
                None,
            )
            if victim is None:
                return
            entry = self._cache.pop(victim)
            self._cache_bytes -= entry.byte_count

    def _select_legacy(self, reason: str) -> None:
        with self._lock:
            if self._legacy_selected:
                return
            self._legacy_selected = True
            self._failure_reason = reason
            self._cache.clear()
            self._cache_bytes = 0
            self._active_tokens.clear()
        logger.error(
            "Sample Piano unavailable; selecting Legacy Piano for this session: %s",
            reason,
        )


def _render_sample_voice(
    voice: SampleVoice,
    duration: float,
    sample_rate: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
    release_seconds: float | None = None,
) -> array:
    source_rate = voice.soft_buffer.sample_rate
    channels = voice.soft_buffer.channels
    source_frames = min(voice.soft_buffer.frame_count, voice.hard_buffer.frame_count)
    step = voice.pitch_ratio * source_rate / sample_rate
    release = release_seconds if release_seconds is not None else ACOUSTIC_PIANO_V2.release_seconds(
        voice.pitch,
        voice.velocity,
        0.8,
        ReleaseMode.NATURAL,
    )
    note_off_frame = int(duration * sample_rate)
    release_frames = max(1, int(release * sample_rate))
    maximum_frames = int((duration + release) * sample_rate)
    output_frames = min(maximum_frames, int(source_frames / step))
    left_pan = sqrt((1 - voice.pan) / 2)
    right_pan = sqrt((1 + voice.pan) / 2)
    result = array("h")
    for frame in range(output_frames):
        if (
            should_cancel is not None
            and frame % 4096 == 0
            and should_cancel()
        ):
            return array("h")
        release_progress = min(1.0, max(0.0, (frame - note_off_frame) / release_frames))
        release_gain = (1.0 - release_progress) ** ACOUSTIC_PIANO_V2.release_curve
        position = frame * step
        index = min(source_frames - 1, int(position))
        next_index = min(source_frames - 1, index + 1)
        fraction = position - index
        for channel, pan_gain in ((0, left_pan), (1, right_pan)):
            offset = index * channels + channel
            next_offset = next_index * channels + channel
            soft = (
                voice.soft_buffer.samples[offset] * (1 - fraction)
                + voice.soft_buffer.samples[next_offset] * fraction
            )
            hard = (
                voice.hard_buffer.samples[offset] * (1 - fraction)
                + voice.hard_buffer.samples[next_offset] * fraction
            )
            value = (
                (soft * voice.soft_gain + hard * voice.hard_gain)
                * voice.velocity_gain
                * pan_gain
                * release_gain
            )
            result.append(_clamp_int16(value))
    return result


_LOAD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sample-piano-preload")


def _schedule_background(job: Callable[[], None]) -> None:
    # Each engine deduplicates pending roots (18 for bundled assets). The app
    # owns three banks, so queued work is bounded as well as active decoding.
    _LOAD_POOL.submit(job)


def _validate_pitch(pitch: int) -> None:
    if isinstance(pitch, bool) or not isinstance(pitch, int) or not 21 <= pitch <= 108:
        raise ValueError("pitch must be an integer from 21 (A0) to 108 (C8)")


def _validate_velocity(velocity: int) -> None:
    if isinstance(velocity, bool) or not isinstance(velocity, int) or not 1 <= velocity <= 127:
        raise ValueError("velocity must be an integer from 1 to 127")


def _required_mapping(payload: dict, name: str) -> dict:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"manifest {name} must be an object")
    return value


def _required_string(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest {name} must be a non-empty string")
    return value


def _required_int(payload: dict, name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"manifest {name} must be an integer")
    return value


def _required_positive_int(payload: dict, name: str) -> int:
    value = _required_int(payload, name)
    if value <= 0:
        raise ValueError(f"manifest {name} must be positive")
    return value


def _clamp_int16(value: float) -> int:
    return max(-32_768, min(32_767, int(value)))
