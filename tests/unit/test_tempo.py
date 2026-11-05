import pytest

from ai_piano_studio.domain.tempo import (
    PPQ,
    TempoMap,
    validate_bpm,
    validate_snap_division,
    validate_time_signature,
)


def test_default_tempo_is_120_bpm_4_4() -> None:
    tempo = TempoMap()
    assert (tempo.bpm, tempo.beats_per_bar, tempo.beat_unit) == (120, 4, 4)
    assert tempo.seconds_per_beat == 0.5
    assert tempo.bar_duration_seconds == 2.0


def test_seconds_beats_ticks_conversion_layer() -> None:
    tempo = TempoMap()
    assert tempo.seconds_to_beats(1.0) == 2.0
    assert tempo.beats_to_seconds(2.0) == 1.0
    assert tempo.seconds_to_ticks(1.0) == 2 * PPQ
    assert tempo.ticks_to_seconds(2 * PPQ) == 1.0
    assert tempo.beats_to_ticks(0.25) == PPQ // 4


def test_conversion_follows_project_bpm() -> None:
    slow = TempoMap(bpm=60)
    assert slow.seconds_per_beat == 1.0
    assert slow.seconds_to_ticks(1.0) == PPQ
    fast = TempoMap(bpm=240)
    assert fast.seconds_to_ticks(1.0) == 4 * PPQ


def test_bar_length_respects_time_signature() -> None:
    assert TempoMap(beats_per_bar=3, beat_unit=4).bar_duration_seconds == 1.5
    assert TempoMap(beats_per_bar=6, beat_unit=8).bar_duration_seconds == 1.5


def test_snap_step_seconds_for_divisions() -> None:
    tempo = TempoMap()
    assert tempo.snap_step_seconds(4) == 0.5
    assert tempo.snap_step_seconds(8) == 0.25
    assert tempo.snap_step_seconds(16) == 0.125
    assert TempoMap(bpm=60).snap_step_seconds(8) == 0.5


def test_rescale_seconds_preserves_beat_position() -> None:
    old = TempoMap()
    new = TempoMap(bpm=60)
    assert new.rescale_seconds(0.5, old) == 1.0
    assert old.rescale_seconds(1.0, new) == 0.5


@pytest.mark.parametrize("bpm", (59, 301, 0, -10, True, 120.5, "120"))
def test_validate_bpm_rejects_values_outside_60_to_300(bpm) -> None:
    with pytest.raises(ValueError, match="bpm"):
        validate_bpm(bpm)


@pytest.mark.parametrize("bpm", (60, 120, 300))
def test_validate_bpm_accepts_bounds(bpm: int) -> None:
    assert validate_bpm(bpm) == bpm


@pytest.mark.parametrize("signature", ((4, 4), (3, 4), (6, 8), (3, 8), (2, 2)))
def test_validate_time_signature_accepts_supported(signature) -> None:
    assert validate_time_signature(*signature) == signature


@pytest.mark.parametrize("signature", ((5, 4), (2, 16), (7, 8), (4, 8), (3.0, 8), (2, 2.0), (True, 4)))
def test_validate_time_signature_rejects_unsupported(signature) -> None:
    with pytest.raises(ValueError, match="time signature"):
        validate_time_signature(*signature)


def test_validate_snap_division() -> None:
    assert validate_snap_division(16) == 16
    with pytest.raises(ValueError, match="snap division"):
        validate_snap_division(32)
