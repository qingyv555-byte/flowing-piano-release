from __future__ import annotations

from math import cos, pi, sin, sqrt
from typing import Sequence

import pytest

from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, ReleaseMode
from ai_piano_studio.audio.synth import SAMPLE_RATE, render_note


def _normalized_attack_difference(samples: Sequence[int]) -> float:
    magnitude = sum(abs(sample) for sample in samples)
    return sum(abs(right - left) for left, right in zip(samples, samples[1:])) / magnitude


def _rms(samples: Sequence[int]) -> float:
    return sqrt(sum(sample * sample for sample in samples) / len(samples))


def test_low_energetic_note_releases_longer_than_quiet_high_note() -> None:
    low = ACOUSTIC_PIANO_V2.release_seconds(36, 127, 1.0, ReleaseMode.NATURAL)
    high = ACOUSTIC_PIANO_V2.release_seconds(84, 30, 0.05, ReleaseMode.NATURAL)

    assert 0.20 <= low <= 1.20
    assert 0.20 <= high <= 1.20
    assert low > high


def test_stop_release_is_shorter_than_safe_and_natural_release() -> None:
    stop = ACOUSTIC_PIANO_V2.release_seconds(60, 90, 0.8, ReleaseMode.STOP)
    safe = ACOUSTIC_PIANO_V2.release_seconds(60, 90, 0.8, ReleaseMode.SAFE)
    natural = ACOUSTIC_PIANO_V2.release_seconds(60, 90, 0.8, ReleaseMode.NATURAL)

    assert 0 < stop < safe < natural


def test_acoustic_stop_release_is_the_shared_twelve_ms_transport_fade() -> None:
    assert ACOUSTIC_PIANO_V2.release_seconds(
        60, 90, 0.8, ReleaseMode.STOP
    ) == pytest.approx(0.012)


def test_acoustic_profile_renders_a_four_second_held_voice() -> None:
    assert ACOUSTIC_PIANO_V2.render_hold_seconds == 4.0


def test_hard_c4_has_brighter_normalized_attack_than_soft_c4() -> None:
    soft = render_note(60, 0.35, 35)
    hard = render_note(60, 0.35, 120)
    attack_frames = int(0.035 * SAMPLE_RATE)

    assert _normalized_attack_difference(hard[:attack_frames]) > _normalized_attack_difference(
        soft[:attack_frames]
    )


def test_registers_have_distinct_normalized_harmonic_ratios() -> None:
    ratios = []
    for pitch in (36, 60, 84):
        gains = ACOUSTIC_PIANO_V2.partial_gains(pitch, 100)
        ratios.append(tuple(round(gain / gains[0], 4) for gain in gains[1:]))

    assert len(set(ratios)) == 3


def test_rendered_registers_have_distinct_normalized_second_harmonics() -> None:
    ratios = []
    for pitch in (36, 60, 84):
        samples = render_note(pitch, 0.30, 100)
        ratios.append(_harmonic_energy(samples, pitch, 2) / _harmonic_energy(samples, pitch, 1))

    assert max(ratios) - min(ratios) > 0.05


def test_held_note_decays_before_note_off() -> None:
    samples = render_note(60, 0.75, 100)
    early = samples[int(0.10 * SAMPLE_RATE): int(0.18 * SAMPLE_RATE)]
    late = samples[int(0.55 * SAMPLE_RATE): int(0.63 * SAMPLE_RATE)]

    assert _rms(late) < _rms(early)


def test_hard_velocity_changes_hammer_transient_not_only_amplitude() -> None:
    soft = render_note(60, 0.30, 35)
    hard = render_note(60, 0.30, 120)
    attack_frames = int(0.020 * SAMPLE_RATE)

    assert ACOUSTIC_PIANO_V2.hammer_gain(120) > ACOUSTIC_PIANO_V2.hammer_gain(35)
    assert _normalized_attack_difference(hard[:attack_frames]) > _normalized_attack_difference(
        soft[:attack_frames]
    )


def test_hard_velocity_has_more_normalized_fourth_harmonic_energy() -> None:
    soft = render_note(60, 0.30, 35)
    hard = render_note(60, 0.30, 120)
    soft_ratio = _harmonic_energy(soft, 60, 4) / _harmonic_energy(soft, 60, 1)
    hard_ratio = _harmonic_energy(hard, 60, 4) / _harmonic_energy(hard, 60, 1)

    assert hard_ratio > soft_ratio + 0.03


def test_rendering_is_deterministic_and_rejects_invalid_values() -> None:
    assert render_note(60, 0.30, 90) == render_note(60, 0.30, 90)

    with pytest.raises(ValueError):
        render_note(-1, 0.30, 90)
    with pytest.raises(ValueError):
        render_note(60, 0.0, 90)
    with pytest.raises(ValueError):
        render_note(60, 0.30, 128)
    with pytest.raises(ValueError):
        render_note(60, 0.30, 90, True)


def test_note_with_all_components_above_nyquist_is_silent() -> None:
    samples = render_note(100, 0.30, 120, 1000)

    assert max(abs(sample) for sample in samples) == 0


def _harmonic_energy(samples: Sequence[int], pitch: int, harmonic: int) -> float:
    start = int(0.06 * SAMPLE_RATE)
    frames = samples[start: int(0.14 * SAMPLE_RATE)]
    frequency = 440.0 * 2.0 ** ((pitch - 69) / 12) * harmonic
    cosine = sum(sample * cos(2.0 * pi * frequency * index / SAMPLE_RATE) for index, sample in enumerate(frames))
    sine = sum(sample * sin(2.0 * pi * frequency * index / SAMPLE_RATE) for index, sample in enumerate(frames))
    return sqrt(cosine * cosine + sine * sine)
