from __future__ import annotations

from ai_piano_studio.audio.importer import (
    apply_extraction_controls,
    normalize_extraction_controls,
)


def _candidates() -> list[dict[str, object]]:
    return [
        {"pitch": 36, "startTime": 0.01, "duration": 0.48, "confidence": 0.80, "velocity": 90},
        {"pitch": 60, "startTime": 0.01, "duration": 0.50, "confidence": 0.95, "velocity": 80},
        {"pitch": 64, "startTime": 0.01, "duration": 0.20, "confidence": 0.55, "velocity": 70},
        {"pitch": 62, "startTime": 0.51, "duration": 0.03, "confidence": 0.99, "velocity": 70},
        {"pitch": 62, "startTime": 0.74, "duration": 0.26, "confidence": 0.72, "velocity": 75},
        {"pitch": 48, "startTime": 1.01, "duration": 0.48, "confidence": 0.82, "velocity": 88},
        {"pitch": 67, "startTime": 1.01, "duration": 0.48, "confidence": 0.90, "velocity": 88},
        {"pitch": 200, "startTime": 1.0, "duration": 0.5, "confidence": 1.0, "velocity": 90},
    ]


def test_invalid_controls_are_clamped_and_mode_is_safe() -> None:
    controls = normalize_extraction_controls(
        mode="does-not-exist",
        min_note_duration=-5,
        noise_threshold=4,
        quantize_strength=-1,
        simplify_density=4,
        pitch_min=120,
        pitch_max=10,
    )

    assert controls.mode == "auto"
    assert controls.min_note_duration == 0.01
    assert controls.noise_threshold == 1.0
    assert controls.quantize_strength == 0.0
    assert controls.simplify_density == 1.0
    assert (controls.pitch_min, controls.pitch_max) == (21, 108)


def test_quantize_and_filter_are_deterministic_without_mutating_input() -> None:
    source = _candidates()
    result = apply_extraction_controls(
        source,
        tempo_bpm=120,
        mode="auto",
        min_note_duration=0.08,
        noise_threshold=0.6,
        quantize_strength=1.0,
        pitch_min=21,
        pitch_max=108,
    )

    assert source[0]["startTime"] == 0.01
    assert result == apply_extraction_controls(
        source,
        tempo_bpm=120,
        mode="auto",
        min_note_duration=0.08,
        noise_threshold=0.6,
        quantize_strength=1.0,
        pitch_min=21,
        pitch_max=108,
    )
    assert all(
        abs(float(note["startTime"]) / 0.25 - round(float(note["startTime"]) / 0.25)) < 1e-6
        for note in result
    )
    assert all(note["duration"] >= 0.08 for note in result)
    assert all(note["pitch"] != 200 for note in result)
    assert all(float(note["confidence"]) >= 0.6 for note in result)


def test_melody_mode_prefers_a_continuous_single_line() -> None:
    result = apply_extraction_controls(
        _candidates(), mode="melody", min_note_duration=0.08, quantize_strength=0.0
    )

    assert [note["pitch"] for note in result] == [60, 62, 67]
    assert len({note["startTime"] for note in result}) == len(result)


def test_bass_mode_keeps_low_register_and_simplified_mode_reduces_density() -> None:
    bass = apply_extraction_controls(
        _candidates(), mode="bass", min_note_duration=0.08, quantize_strength=0.0
    )
    simplified = apply_extraction_controls(
        _candidates(),
        mode="simplified",
        min_note_duration=0.08,
        simplify_density=0.0,
        quantize_strength=0.0,
    )

    assert [note["pitch"] for note in bass] == [36, 48]
    assert len(simplified) < len(
        apply_extraction_controls(
            _candidates(), mode="auto", min_note_duration=0.08, quantize_strength=0.0
        )
    )
    assert all(note["pitch"] != 200 for note in simplified)
