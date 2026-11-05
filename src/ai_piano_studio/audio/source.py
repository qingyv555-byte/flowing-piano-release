from __future__ import annotations

from abc import ABC, abstractmethod
from array import array
from collections import OrderedDict, deque
from dataclasses import dataclass
import logging
from math import isfinite, pi, sin
from threading import RLock, Thread
from typing import Callable, Iterable

from ai_piano_studio.audio.mixer import PCMContribution, PianoVoiceMixer
from ai_piano_studio.audio.profile import (
    ACOUSTIC_PIANO_V2,
    PianoVoiceProfile,
    ReleaseMode,
)
from ai_piano_studio.audio.sample_piano import LegacyVoice, SamplePianoEngine, SampleVoice
from ai_piano_studio.audio.synth import SAMPLE_RATE, render_note as render_legacy_note
from ai_piano_studio.audio.synth import render_project as render_legacy_project
from ai_piano_studio.domain.note import Note
from ai_piano_studio.audio.tone import PianoToneCoordinator, PianoToneSelection


logger = logging.getLogger(__name__)

_LEGACY_REALTIME_CACHE_ENTRIES = 16
_LEGACY_REALTIME_WARMUP_QUEUE_ENTRIES = 16
_MAX_RELEASE_SECONDS = 5.0


class PianoSoundSource(ABC):
    """Common source operations shared by Legacy and manifest-backed piano sound."""

    @abstractmethod
    def start_voice(
        self, voice_id: str, pitch: int, velocity: int
    ) -> SampleVoice | LegacyVoice:
        raise NotImplementedError

    @abstractmethod
    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode = ReleaseMode.NATURAL,
        *,
        release_seconds: float | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def release_all(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        raise NotImplementedError

    @abstractmethod
    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        raise NotImplementedError

    @abstractmethod
    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE
    ) -> array:
        raise NotImplementedError

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        return self.render_project(notes)

    def snapshot_renderer(self) -> PianoSoundSource:
        """Return an immutable renderer for one playback/export operation."""
        return self

    @property
    def output_channels(self) -> int:
        return 1


class LegacyPianoSource(PianoSoundSource):
    """Compatibility facade over the existing Acoustic Piano v2 renderer."""

    def __init__(
        self,
        profile: PianoVoiceProfile = ACOUSTIC_PIANO_V2,
        *,
        realtime_scheduler: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if not isinstance(profile, PianoVoiceProfile):
            logger.error("Failed to construct Legacy Piano source: invalid profile")
            raise ValueError("profile must be a PianoVoiceProfile")
        self._profile = profile
        self._voices: dict[str, LegacyVoice] = {}
        self._lock = RLock()
        self._realtime_scheduler = realtime_scheduler or _schedule_daemon
        self._realtime_cache: OrderedDict[tuple[int, int], memoryview] = OrderedDict()
        self._warming_keys: set[tuple[int, int]] = set()
        self._warmup_queue: deque[tuple[int, int]] = deque()
        self._warmup_scheduled = False

    def start_voice(self, voice_id: str, pitch: int, velocity: int) -> LegacyVoice:
        try:
            _validate_voice_id(voice_id)
            key = pitch, velocity
            with self._lock:
                samples = self._realtime_cache.get(key)
                if samples is not None:
                    self._realtime_cache.move_to_end(key)
                elif key not in self._warming_keys:
                    if len(self._warmup_queue) < _LEGACY_REALTIME_WARMUP_QUEUE_ENTRIES:
                        self._warming_keys.add(key)
                        self._warmup_queue.append(key)
                        should_schedule = not self._warmup_scheduled
                        self._warmup_scheduled = True
                    else:
                        should_schedule = False
                else:
                    should_schedule = False
            if samples is None:
                if should_schedule:
                    self._schedule_realtime_render()
                samples, sample_rate = _legacy_voice_pcm(pitch)
            else:
                sample_rate = SAMPLE_RATE
            voice = LegacyVoice(
                pitch=pitch,
                velocity=velocity,
                reason="Legacy Piano source",
                source=samples,
                looping=True,
                sample_rate=sample_rate,
            )
            self._voices[voice_id] = voice
            return voice
        except Exception:
            logger.exception("Failed to start Legacy Piano voice %r", voice_id)
            raise

    def _schedule_realtime_render(self) -> None:
        def render() -> None:
            while True:
                with self._lock:
                    if not self._warmup_queue:
                        self._warmup_scheduled = False
                        return
                    key = self._warmup_queue.popleft()
                try:
                    samples = _readonly_pcm(
                        render_legacy_note(
                            key[0],
                            self._profile.render_hold_seconds,
                            key[1],
                            profile=self._profile,
                        )
                    )
                    with self._lock:
                        self._realtime_cache[key] = samples
                        self._realtime_cache.move_to_end(key)
                        while len(self._realtime_cache) > _LEGACY_REALTIME_CACHE_ENTRIES:
                            self._realtime_cache.popitem(last=False)
                except Exception:
                    logger.exception("Failed to warm Legacy Piano pitch=%s velocity=%s", *key)
                finally:
                    with self._lock:
                        self._warming_keys.discard(key)

        try:
            self._realtime_scheduler(render)
        except Exception:
            with self._lock:
                self._warmup_scheduled = False
                self._warming_keys.clear()
                self._warmup_queue.clear()
            logger.exception("Failed to schedule Legacy Piano warm-up")

    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode = ReleaseMode.NATURAL,
        *,
        release_seconds: float | None = None,
    ) -> None:
        try:
            _validate_voice_id(voice_id)
            _validate_release_mode(mode)
            _validate_release_seconds(release_seconds)
            self._voices.pop(voice_id, None)
        except Exception:
            logger.exception("Failed to release Legacy Piano voice %r", voice_id)
            raise

    def release_all(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        try:
            _validate_release_mode(mode)
            self._voices.clear()
        except Exception:
            logger.exception("Failed to release all Legacy Piano voices")
            raise

    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        try:
            return render_legacy_note(
                pitch,
                duration,
                velocity,
                sample_rate,
                profile=self._profile,
            )
        except Exception:
            logger.exception(
                "Failed to render Legacy Piano note pitch=%r velocity=%r",
                pitch,
                velocity,
            )
            raise

    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE
    ) -> array:
        try:
            return render_legacy_project(
                notes,
                sample_rate,
                profile=self._profile,
            )
        except Exception:
            logger.exception("Failed to render Legacy Piano project")
            raise

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        return render_legacy_project(
            notes,
            profile=self._profile,
            should_cancel=should_cancel,
        )


class SamplePianoSource(PianoSoundSource):
    """Facade that delegates selection and offline rendering to the sample engine."""

    def __init__(
        self, engine: SamplePianoEngine, mixer: PianoVoiceMixer | None = None
    ) -> None:
        if not isinstance(engine, SamplePianoEngine):
            logger.error("Failed to construct Sample Piano source: invalid engine")
            raise ValueError("engine must be a SamplePianoEngine")
        self._engine = engine
        self._mixer = mixer
        self._voices: dict[str, list[SampleVoice | LegacyVoice]] = {}
        if mixer is not None:
            mixer.voiceFinished.connect(self._finish_voice)

    @property
    def uses_legacy(self) -> bool:
        return self._engine.legacy_selected

    def start_voice(
        self, voice_id: str, pitch: int, velocity: int
    ) -> SampleVoice | LegacyVoice:
        try:
            _validate_voice_id(voice_id)
            voice = self._engine.note_on(pitch, velocity)
            if self._mixer is None:
                previous = self._voices.pop(voice_id, [])
                for item in previous:
                    self._engine.release_voice(item)
                self._voices[voice_id] = [voice]
            else:
                self._voices.setdefault(voice_id, []).append(voice)
                try:
                    self._start_mixer_voice(voice_id, voice)
                except Exception:
                    pending = self._voices.get(voice_id)
                    if pending and pending[-1] is voice:
                        pending.pop()
                        if not pending:
                            self._voices.pop(voice_id, None)
                    self._engine.release_voice(voice)
                    raise
            return voice
        except Exception:
            logger.exception("Failed to start Sample Piano voice %r", voice_id)
            raise

    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode = ReleaseMode.NATURAL,
        *,
        release_seconds: float | None = None,
    ) -> None:
        try:
            _validate_voice_id(voice_id)
            _validate_release_mode(mode)
            release_seconds = _validate_release_seconds(release_seconds)
            if self._mixer is not None:
                if release_seconds is None:
                    self._mixer.note_off(voice_id, mode)
                else:
                    self._mixer.note_off(
                        voice_id, mode, release_seconds=release_seconds
                    )
            else:
                voices = self._voices.pop(voice_id, [])
                for voice in voices:
                    self._engine.release_voice(voice)
        except Exception:
            logger.exception("Failed to release Sample Piano voice %r", voice_id)
            raise

    def release_all(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        try:
            _validate_release_mode(mode)
            if self._mixer is not None:
                self._mixer.release_all(mode)
            else:
                voices = tuple(
                    voice for pending in self._voices.values() for voice in pending
                )
                self._voices.clear()
                for voice in voices:
                    self._engine.release_voice(voice)
        except Exception:
            logger.exception("Failed to release all Sample Piano voices")
            raise

    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        try:
            return self._engine.render_note(pitch, duration, velocity, sample_rate)
        except Exception:
            logger.exception(
                "Failed to render Sample Piano note pitch=%r velocity=%r",
                pitch,
                velocity,
            )
            raise

    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE
    ) -> array:
        try:
            return self._engine.render_project(notes, sample_rate)
        except Exception:
            logger.exception("Failed to render Sample Piano project")
            raise

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        return self._engine.render_project(notes, should_cancel=should_cancel)

    def _start_mixer_voice(
        self, voice_id: str, voice: SampleVoice | LegacyVoice
    ) -> None:
        assert self._mixer is not None
        if isinstance(voice, SampleVoice):
            self._mixer.start_contributions(
                voice_id,
                voice.pitch,
                voice.velocity,
                _sample_voice_contributions(voice, 1.0),
                owner_tag="sample-piano",
            )
        elif voice.source is not None:
            self._mixer.start_voice(
                voice_id,
                voice.pitch,
                voice.velocity,
                voice.source,
                source_sample_rate=voice.sample_rate,
                owner_tag="sample-piano",
                looping=voice.looping,
            )
        else:
            fallback_pcm, fallback_sample_rate = _legacy_voice_pcm(voice.pitch)
            self._mixer.start_voice(
                voice_id,
                voice.pitch,
                voice.velocity,
                fallback_pcm,
                source_sample_rate=fallback_sample_rate,
                owner_tag="sample-piano",
                looping=True,
            )

    def _finish_voice(self, voice_id: str) -> None:
        try:
            pending = self._voices.get(voice_id)
            if not pending:
                return
            voice = pending.pop(0)
            if not pending:
                self._voices.pop(voice_id, None)
            self._engine.release_voice(voice)
        except Exception:
            logger.exception("Failed to finish Sample Piano voice %r", voice_id)


@dataclass(frozen=True, slots=True)
class ToneVoice:
    selection: PianoToneSelection
    bank_voices: tuple[tuple[SamplePianoEngine, SampleVoice | LegacyVoice], ...]


class ContinuousPianoToneSource(PianoSoundSource):
    """Snapshot each committed tone selection and retain its engine tokens."""

    def __init__(
        self,
        coordinator: PianoToneCoordinator,
        mixer: PianoVoiceMixer,
        legacy_source: LegacyPianoSource | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._mixer = mixer
        self._legacy_source = legacy_source or LegacyPianoSource()
        self._voices: dict[str, list[ToneVoice]] = {}
        self._legacy_voices: dict[str, list[LegacyVoice]] = {}
        mixer.voiceFinished.connect(self._finish_voice)

    def set_tone(self, position: float) -> PianoToneSelection | None:
        return self._coordinator.commit(position)

    def prefetch_pitches(self, pitches: Iterable[int]) -> None:
        pitches = tuple(dict.fromkeys(pitches))
        selection = self._coordinator.selection()
        if selection is not None:
            for engine in (selection.primary, selection.secondary):
                if engine is not None:
                    engine.prefetch_pitches(pitches)

    def ready_for_pitches(self, pitches: Iterable[int]) -> bool:
        pitches = tuple(pitches)
        selection = self._coordinator.selection()
        return selection is None or all(engine.ready_for_pitches(pitches)
            for engine in (selection.primary, selection.secondary) if engine is not None)

    @property
    def mixer(self) -> PianoVoiceMixer:
        return self._mixer

    @property
    def output_channels(self) -> int:
        return self.snapshot_renderer().output_channels

    def start_voice(
        self, voice_id: str, pitch: int, velocity: int
    ) -> ToneVoice | LegacyVoice:
        try:
            _validate_voice_id(voice_id)
            # Project notes support the full MIDI range, while the bundled
            # sample banks intentionally cover only the acoustic piano range
            # A0-C8. Keep valid edge notes audible through the legacy source
            # instead of letting the sample engine raise into the Qt event loop.
            if (
                isinstance(pitch, int)
                and not isinstance(pitch, bool)
                and 0 <= pitch <= 127
                and not 21 <= pitch <= 108
            ):
                return self._start_legacy_fallback_voice(voice_id, pitch, velocity)
            selection = self._coordinator.selection()
            if selection is None:
                voice = self._legacy_source.start_voice(voice_id, pitch, velocity)
                self._legacy_voices.setdefault(voice_id, []).append(voice)
                try:
                    assert voice.source is not None
                    self._mixer.start_voice(
                        voice_id,
                        pitch,
                        velocity,
                        voice.source,
                        source_sample_rate=voice.sample_rate,
                        owner_tag="legacy-fallback",
                        looping=voice.looping,
                    )
                except Exception:
                    self._legacy_voices[voice_id].pop()
                    if not self._legacy_voices[voice_id]:
                        self._legacy_voices.pop(voice_id, None)
                    self._legacy_source.note_off(voice_id, ReleaseMode.STOP)
                    raise
                return voice
            engines = (selection.primary,) + (
                () if selection.secondary is None else (selection.secondary,)
            )
            if any(engine.legacy_selected for engine in engines):
                return self._start_legacy_fallback_voice(voice_id, pitch, velocity)
            gains = selection.gains
            acquired: list[tuple[SamplePianoEngine, SampleVoice | LegacyVoice]] = []
            try:
                for engine in engines:
                    acquired.append((engine, engine.note_on(pitch, velocity)))
            except Exception:
                for engine, voice in acquired:
                    engine.release_voice(voice)
                raise
            bank_voices = tuple(acquired)
            tone_voice = ToneVoice(selection, bank_voices)
            contributions: list[PCMContribution] = []
            try:
                for bank_gain, (_engine, voice) in zip(gains, bank_voices):
                    if isinstance(voice, SampleVoice):
                        contributions.extend(
                            _sample_voice_contributions(voice, bank_gain)
                        )
                if not contributions:
                    fallback_pcm, fallback_sample_rate = _legacy_voice_pcm(pitch)
                    contributions.append(
                        PCMContribution(
                            fallback_pcm,
                            fallback_sample_rate,
                            1,
                            1.0,
                            0.0,
                            True,
                        )
                    )
                self._voices.setdefault(voice_id, []).append(tone_voice)
                self._mixer.start_contributions(
                    voice_id,
                    pitch,
                    velocity,
                    tuple(contributions),
                    owner_tag="continuous-tone",
                )
            except Exception:
                pending = self._voices.get(voice_id)
                if pending and pending[-1] is tone_voice:
                    pending.pop()
                    if not pending:
                        self._voices.pop(voice_id, None)
                for engine, voice in bank_voices:
                    engine.release_voice(voice)
                raise
            return tone_voice
        except Exception:
            logger.exception("Failed to start continuous piano tone voice %r", voice_id)
            raise

    def _start_legacy_fallback_voice(
        self, voice_id: str, pitch: int, velocity: int
    ) -> LegacyVoice:
        voice = self._legacy_source.start_voice(voice_id, pitch, velocity)
        self._legacy_voices.setdefault(voice_id, []).append(voice)
        try:
            assert voice.source is not None
            self._mixer.start_voice(
                voice_id,
                pitch,
                velocity,
                voice.source,
                source_sample_rate=voice.sample_rate,
                owner_tag="legacy-fallback",
                looping=voice.looping,
            )
        except Exception:
            self._legacy_voices[voice_id].pop()
            if not self._legacy_voices[voice_id]:
                self._legacy_voices.pop(voice_id, None)
            self._legacy_source.note_off(voice_id, ReleaseMode.STOP)
            raise
        return voice

    def note_off(
        self,
        voice_id: str,
        mode: ReleaseMode = ReleaseMode.NATURAL,
        *,
        release_seconds: float | None = None,
    ) -> None:
        try:
            _validate_voice_id(voice_id)
            _validate_release_mode(mode)
            release_seconds = _validate_release_seconds(release_seconds)
            if release_seconds is None:
                self._mixer.note_off(voice_id, mode)
            else:
                self._mixer.note_off(
                    voice_id, mode, release_seconds=release_seconds
                )
        except Exception:
            logger.exception("Failed to release continuous piano tone voice %r", voice_id)
            raise

    def release_all(self, mode: ReleaseMode = ReleaseMode.STOP) -> None:
        try:
            _validate_release_mode(mode)
            self._mixer.release_all(mode)
        except Exception:
            logger.exception("Failed to release all continuous piano tone voices")
            raise

    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        try:
            return self.snapshot_renderer().render_note(
                pitch, duration, velocity, sample_rate
            )
        except Exception:
            logger.exception(
                "Failed to render continuous piano tone note pitch=%r velocity=%r",
                pitch,
                velocity,
            )
            raise

    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE
    ) -> array:
        try:
            return self.snapshot_renderer().render_project(notes, sample_rate)
        except Exception:
            logger.exception("Failed to render continuous piano tone project")
            raise

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        renderer = self.snapshot_renderer()
        return _render_snapshot_cancellable(renderer, notes, should_cancel)

    def snapshot_renderer(self) -> _ToneRenderSnapshot:
        selection = self._coordinator.selection()
        if selection is None:
            return self._legacy_source  # type: ignore[return-value]
        return _ToneRenderSnapshot(selection, self._legacy_source)

    def _finish_voice(self, voice_id: str) -> None:
        try:
            legacy = self._legacy_voices.get(voice_id)
            if legacy:
                legacy.pop(0)
                if not legacy:
                    self._legacy_voices.pop(voice_id, None)
                self._legacy_source.note_off(voice_id, ReleaseMode.STOP)
                return
            pending = self._voices.get(voice_id)
            if not pending:
                return
            voice = pending.pop(0)
            if not pending:
                self._voices.pop(voice_id, None)
            for engine, bank_voice in voice.bank_voices:
                engine.release_voice(bank_voice)
        except Exception:
            logger.exception("Failed to finish continuous piano tone voice %r", voice_id)


@dataclass(slots=True)
class _ToneRenderSnapshot:
    selection: PianoToneSelection
    legacy_source: LegacyPianoSource
    _output_channels: int = 2

    @property
    def output_channels(self) -> int:
        return self._output_channels

    def render_note(
        self,
        pitch: int,
        duration: float,
        velocity: int,
        sample_rate: int = SAMPLE_RATE,
    ) -> array:
        primary = self.selection.primary.render_note(
            pitch, duration, velocity, sample_rate
        )
        secondary = self.selection.secondary
        if self.selection.primary.legacy_selected:
            return self._render_legacy_note(pitch, duration, velocity, sample_rate)
        if secondary is None:
            return primary
        secondary_pcm = secondary.render_note(
            pitch, duration, velocity, sample_rate
        )
        if secondary.legacy_selected:
            return self._render_legacy_note(pitch, duration, velocity, sample_rate)
        return _blend_pcm(
            primary,
            self.selection.primary_gain,
            secondary_pcm,
            self.selection.secondary_gain,
        )

    def render_project(
        self, notes: Iterable[Note], sample_rate: int = SAMPLE_RATE
    ) -> array:
        notes = tuple(notes)
        primary = self.selection.primary.render_project(notes, sample_rate)
        secondary = self.selection.secondary
        if self.selection.primary.legacy_selected:
            return self._render_legacy_project(notes, sample_rate)
        if secondary is None:
            return primary
        secondary_pcm = secondary.render_project(notes, sample_rate)
        if secondary.legacy_selected:
            return self._render_legacy_project(notes, sample_rate)
        return _blend_pcm(
            primary,
            self.selection.primary_gain,
            secondary_pcm,
            self.selection.secondary_gain,
        )

    def render_project_cancellable(
        self, notes: Iterable[Note], should_cancel: Callable[[], bool]
    ) -> array:
        notes = tuple(notes)
        if should_cancel():
            return array("h")
        if self.selection.primary.legacy_selected:
            self._output_channels = 1
            return self.legacy_source.render_project_cancellable(notes, should_cancel)
        primary = self.selection.primary.render_project(
            notes, should_cancel=should_cancel
        )
        if should_cancel():
            return array("h")
        secondary = self.selection.secondary
        if secondary is None:
            return primary
        if secondary.legacy_selected:
            self._output_channels = 1
            return self.legacy_source.render_project_cancellable(notes, should_cancel)
        secondary_pcm = secondary.render_project(notes, should_cancel=should_cancel)
        if should_cancel():
            return array("h")
        return _blend_pcm(
            primary,
            self.selection.primary_gain,
            secondary_pcm,
            self.selection.secondary_gain,
            should_cancel=should_cancel,
        )

    def _render_legacy_note(
        self, pitch: int, duration: float, velocity: int, sample_rate: int
    ) -> array:
        self._output_channels = 1
        return self.legacy_source.render_note(pitch, duration, velocity, sample_rate)

    def _render_legacy_project(
        self, notes: Iterable[Note], sample_rate: int
    ) -> array:
        self._output_channels = 1
        return self.legacy_source.render_project(notes, sample_rate)


def _blend_pcm(
    primary: array,
    primary_gain: float,
    secondary: array,
    secondary_gain: float,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> array:
    length = max(len(primary), len(secondary))
    blended = array("h")
    for index in range(length):
        if (
            should_cancel is not None
            and index % 4096 == 0
            and should_cancel()
        ):
            return array("h")
        value = (
            (primary[index] if index < len(primary) else 0) * primary_gain
            + (secondary[index] if index < len(secondary) else 0) * secondary_gain
        )
        blended.append(max(-32_768, min(32_767, round(value))))
    return blended


def _render_snapshot_cancellable(
    renderer: PianoSoundSource | _ToneRenderSnapshot,
    notes: Iterable[Note],
    should_cancel: Callable[[], bool],
) -> array:
    return renderer.render_project_cancellable(notes, should_cancel)


def _sample_voice_contributions(
    voice: SampleVoice, bank_gain: float
) -> tuple[PCMContribution, ...]:
    playback_rate = max(1, round(voice.soft_buffer.sample_rate * voice.pitch_ratio))
    layers = (
        (voice.soft_buffer, bank_gain * voice.soft_gain),
        (voice.hard_buffer, bank_gain * voice.hard_gain),
    )
    return tuple(
        PCMContribution(
            buffer.samples,
            playback_rate,
            buffer.channels,
            gain,
            voice.pan,
        )
        for buffer, gain in layers
        if gain > 0.0
    )


def _legacy_voice_pcm(pitch: int) -> tuple[memoryview, int]:
    return _LEGACY_FALLBACK_PCM[pitch]


def _build_legacy_fallback_pcm(pitch: int) -> tuple[memoryview, int]:
    frequency = 440.0 * 2 ** ((pitch - 69) / 12)
    frames = 16
    samples = array("h", (
        round(9_000 * (0.90 * sin(2.0 * pi * frame / frames) + 0.10 * sin(4.0 * pi * frame / frames)))
        for frame in range(frames)
    ))
    return (
        memoryview(samples.tobytes()).cast("h").toreadonly(),
        max(1, round(frames * frequency)),
    )


def _readonly_pcm(samples: Iterable[int]) -> memoryview:
    pcm = array("h", samples)
    return memoryview(pcm.tobytes()).cast("h").toreadonly()


def _schedule_daemon(job: Callable[[], None]) -> None:
    Thread(target=job, daemon=True).start()


_LEGACY_FALLBACK_PCM = {pitch: _build_legacy_fallback_pcm(pitch) for pitch in range(128)}


def _validate_voice_id(voice_id: str) -> None:
    if not isinstance(voice_id, str) or not voice_id:
        raise ValueError("voice_id must be a non-empty string")


def _validate_release_mode(mode: ReleaseMode) -> None:
    if not isinstance(mode, ReleaseMode):
        raise ValueError("mode must be a ReleaseMode")


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
