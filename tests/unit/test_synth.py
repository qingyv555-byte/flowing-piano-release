import io
import wave
from array import array
from dataclasses import replace
from math import sqrt
from time import perf_counter

import pytest

import ai_piano_studio.audio.synth as synth
from ai_piano_studio.audio.synth import (
    SAMPLE_RATE,
    encode_wav,
    midi_to_frequency,
    render_note,
    render_project,
)
from ai_piano_studio.audio.profile import ACOUSTIC_PIANO_V2, LEGACY_PIANO
from ai_piano_studio.domain.note import Note


def test_midi_to_frequency_known_pitches() -> None:
    assert midi_to_frequency(69) == pytest.approx(440.0)
    assert midi_to_frequency(60) == pytest.approx(261.6256, abs=1e-3)
    assert midi_to_frequency(81) == pytest.approx(880.0)


def test_midi_to_frequency_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        midi_to_frequency(128)
    with pytest.raises(ValueError):
        midi_to_frequency(-1)


def test_render_note_length_includes_release_tail() -> None:
    samples = render_note(60, 0.5, 100, SAMPLE_RATE)
    assert len(samples) > int(0.5 * SAMPLE_RATE)
    assert len(samples) <= int(1.7 * SAMPLE_RATE)


def test_c4_release_length_matches_hand_calculated_duration() -> None:
    assert len(render_note(60, 0.5, 90, SAMPLE_RATE)) == 51314


def test_render_note_stays_within_int16_bounds() -> None:
    samples = render_note(21, 1.0, 127, SAMPLE_RATE)
    assert max(samples) <= 32767
    assert min(samples) >= -32768


def test_render_note_velocity_scales_amplitude() -> None:
    quiet = render_note(60, 0.3, 40, SAMPLE_RATE)
    loud = render_note(60, 0.3, 120, SAMPLE_RATE)
    assert max(loud) > max(quiet) > 0


def test_render_note_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        render_note(60, 0.0, 75)
    with pytest.raises(ValueError):
        render_note(60, 0.5, 200)
    with pytest.raises(ValueError):
        render_note(60, 0.5, 75, 0)


def test_encode_wav_roundtrips_through_wave_module() -> None:
    samples = render_note(69, 0.2, 90, SAMPLE_RATE)
    payload = encode_wav(samples, SAMPLE_RATE)
    with wave.open(io.BytesIO(payload), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.readframes(wav.getnframes()) == samples.tobytes()


def test_encode_wav_preserves_stereo_frame_timing() -> None:
    """Fails if sample-piano stereo PCM is mislabeled as mono or double duration."""
    samples = array("h", [100, -100, 200, -200])

    payload = encode_wav(samples, SAMPLE_RATE, channels=2)

    with wave.open(io.BytesIO(payload), "rb") as wav:
        assert wav.getnchannels() == 2
        assert wav.getnframes() == 2
        assert wav.readframes(2) == samples.tobytes()


def test_render_project_empty_returns_empty_buffer() -> None:
    assert len(render_project(())) == 0


def test_render_project_covers_last_note_end() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.5),
    )
    samples = render_project(notes, SAMPLE_RATE)
    assert len(samples) > int(1.5 * SAMPLE_RATE)
    assert len(samples) <= int(2.7 * SAMPLE_RATE) + 1


def test_render_project_places_note_at_its_start_time() -> None:
    notes = (Note.create(id="a", pitch=69, start_time=0.5, duration=0.3, velocity=120),)
    samples = render_project(notes, SAMPLE_RATE)
    before = samples[: int(0.4 * SAMPLE_RATE)]
    after_onset = samples[int(0.52 * SAMPLE_RATE): int(0.7 * SAMPLE_RATE)]
    assert max(abs(value) for value in before) == 0
    assert max(abs(value) for value in after_onset) > 0


def test_render_project_normalizes_clipping_mix() -> None:
    notes = tuple(
        Note.create(id=str(index), pitch=60, start_time=0.0, duration=0.5, velocity=127)
        for index in range(8)
    )
    samples = render_project(notes, SAMPLE_RATE)
    assert max(abs(sample) for sample in samples) <= int(32767 * ACOUSTIC_PIANO_V2.peak_amplitude)


def test_successful_default_renders_skip_cancellation_polling(monkeypatch) -> None:
    """Fails if the ordinary no-predicate path takes the cancellable hot loop."""
    def fail_if_polled(*_args) -> bool:
        raise AssertionError("default render should not poll for cancellation")

    monkeypatch.setattr(synth, "_cancelled_at", fail_if_polled)
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.25),
        Note.create(id="b", pitch=64, start_time=0.4, duration=0.25),
    )

    note = render_note(60, 0.25, 90)
    project = render_project(notes)

    assert note
    assert project


def test_render_project_discards_pcm_promptly_when_cancelled() -> None:
    """Fails if default offline rendering ignores the cancellation predicate."""
    notes = tuple(
        Note.create(
            id=str(index), pitch=60 + index % 12, start_time=float(index), duration=1.0
        )
        for index in range(20)
    )
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 5

    started = perf_counter()
    samples = render_project(notes, should_cancel=should_cancel)

    assert perf_counter() - started < 0.2
    assert not samples


def test_release_tail_has_energy_and_ends_smoothly_without_mutating_note() -> None:
    note = Note.create(id="tail", pitch=60, start_time=0.0, duration=0.25, velocity=90)
    samples = render_project((note,))
    note_off = int(note.duration * SAMPLE_RATE)
    tail = samples[note_off:]

    assert note.duration == 0.25
    assert any(tail)
    assert abs(samples[-1]) < 10


def test_release_tail_energy_decays_and_the_pcm_end_is_continuous() -> None:
    samples = render_note(60, 0.25, 90)
    tail = samples[int(0.25 * SAMPLE_RATE):]
    segment = len(tail) // 3
    first = _rms(tail[:segment])
    middle = _rms(tail[segment: 2 * segment])
    last = _rms(tail[-segment:])

    assert first > middle > last
    assert abs(tail[-1] - tail[-2]) <= 4


def test_v2_project_output_is_int16_and_limited_to_profile_peak() -> None:
    notes = tuple(
        Note.create(id=str(index), pitch=60 + index, start_time=0.0, duration=0.4, velocity=127)
        for index in range(8)
    )
    samples = render_project(notes)

    assert samples.typecode == "h"
    pcm_peak = int(32767 * ACOUSTIC_PIANO_V2.peak_amplitude)
    assert max(samples) <= pcm_peak
    assert min(samples) >= -pcm_peak


def test_legacy_piano_remains_selectable_for_ab_comparison() -> None:
    legacy = render_note(60, 0.30, 100, profile=LEGACY_PIANO)
    acoustic = render_note(60, 0.30, 100, profile=ACOUSTIC_PIANO_V2)

    assert legacy.typecode == acoustic.typecode == "h"
    assert legacy != acoustic


def test_profile_equal_to_legacy_constant_uses_profile_renderer() -> None:
    custom_profile = replace(LEGACY_PIANO)

    legacy = render_note(60, 0.30, 100, profile=LEGACY_PIANO)
    custom = render_note(60, 0.30, 100, profile=custom_profile)

    assert custom != legacy


def test_cold_four_second_v2_render_is_not_more_than_twice_as_slow_as_legacy() -> None:
    legacy_started = perf_counter()
    render_note(60, 4.0, 90, profile=LEGACY_PIANO)
    legacy_elapsed = perf_counter() - legacy_started

    v2_started = perf_counter()
    render_note(60, 4.0, 90, profile=ACOUSTIC_PIANO_V2)
    v2_elapsed = perf_counter() - v2_started

    assert v2_elapsed < 0.20
    assert v2_elapsed <= legacy_elapsed * 2


def _rms(samples: tuple[int, ...] | list[int]) -> float:
    return sqrt(sum(sample * sample for sample in samples) / len(samples))
