from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class ReleaseMode(StrEnum):
    NATURAL = "natural"
    SAFE = "safe"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class PianoVoiceProfile:
    name: str
    peak_amplitude: float
    release_curve: float
    safe_release_seconds: float
    stop_fade_seconds: float
    natural_release_low: float
    natural_release_high: float
    render_hold_seconds: float
    max_realtime_voices: int

    def release_seconds(
        self, pitch: int, velocity: int, energy: float, mode: ReleaseMode
    ) -> float:
        _validate_pitch(pitch)
        _validate_velocity(velocity)
        if isinstance(energy, bool) or not isinstance(energy, (int, float)) or not isfinite(energy):
            raise ValueError("energy must be a finite number from 0 to 1")
        if not 0.0 <= energy <= 1.0:
            raise ValueError("energy must be a finite number from 0 to 1")
        if not isinstance(mode, ReleaseMode):
            raise ValueError("mode must be a ReleaseMode")

        if mode is ReleaseMode.STOP:
            return self.stop_fade_seconds
        if mode is ReleaseMode.SAFE:
            return self.safe_release_seconds

        register = min(1.0, max(0.0, (pitch - 36) / 48))
        base = self.natural_release_low + (
            self.natural_release_high - self.natural_release_low
        ) * register
        velocity_scale = 0.90 + 0.20 * (velocity / 127)
        energy_scale = 0.55 + 0.45 * energy
        return min(1.20, max(0.20, base * velocity_scale * energy_scale))

    def decay_seconds(self, pitch: int) -> float:
        _validate_pitch(pitch)
        register = min(1.0, max(0.0, (pitch - 36) / 48))
        return 1.55 - 0.90 * register

    def partial_gains(self, pitch: int, velocity: int) -> tuple[float, ...]:
        _validate_pitch(pitch)
        _validate_velocity(velocity)
        register = min(1.0, max(0.0, (pitch - 36) / 48))
        velocity_ratio = velocity / 127
        high_partial_energy = 0.55 + 0.70 * velocity_ratio
        base = (
            1.0,
            0.56 - 0.22 * register,
            0.34 - 0.24 * register,
            0.20 - 0.16 * register,
            0.11 - 0.09 * register,
            0.055 - 0.045 * register,
        )
        return tuple(
            gain if index == 0 else gain * high_partial_energy
            for index, gain in enumerate(base)
        )

    def attack_seconds(self, velocity: int) -> float:
        _validate_velocity(velocity)
        return 0.014 - 0.010 * (velocity / 127)

    def hammer_gain(self, velocity: int) -> float:
        _validate_velocity(velocity)
        return 0.008 + 0.16 * (velocity / 127) ** 1.5


@dataclass(frozen=True, slots=True)
class SamplePianoProfile:
    """Runtime limits for the optional, manifest-backed sample source."""

    name: str
    cache_byte_limit: int
    preload_low_midi: int
    preload_high_midi: int
    max_root_distance: int
    max_pan: float


def _validate_pitch(pitch: int) -> None:
    if not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127:
        raise ValueError("pitch must be an integer from 0 to 127")


def _validate_velocity(velocity: int) -> None:
    if not isinstance(velocity, int) or isinstance(velocity, bool) or not 0 <= velocity <= 127:
        raise ValueError("velocity must be an integer from 0 to 127")


LEGACY_PIANO = PianoVoiceProfile(
    name="legacy-piano",
    peak_amplitude=0.95,
    release_curve=1.0,
    safe_release_seconds=0.08,
    stop_fade_seconds=0.03,
    natural_release_low=0.08,
    natural_release_high=0.08,
    render_hold_seconds=0.0,
    max_realtime_voices=8,
)

ACOUSTIC_PIANO_V2 = PianoVoiceProfile(
    name="acoustic-piano-v2",
    peak_amplitude=0.92,
    release_curve=1.65,
    safe_release_seconds=0.12,
    stop_fade_seconds=0.012,
    natural_release_low=0.98,
    natural_release_high=0.42,
    render_hold_seconds=4.0,
    max_realtime_voices=8,
)


SAMPLE_PIANO_V1 = SamplePianoProfile(
    name="sample-piano-v1",
    cache_byte_limit=24 * 1024 * 1024,
    preload_low_midi=48,
    preload_high_midi=72,
    max_root_distance=3,
    max_pan=0.12,
)
