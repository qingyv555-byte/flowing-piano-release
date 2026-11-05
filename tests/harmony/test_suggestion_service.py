from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.harmony.suggestion_service import (
    HarmonySuggestionService,
    measure_ticks,
)
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    KeyEstimate,
    MeasureSuggestion,
    MelodyNoteSnapshot,
    ScoreBreakdown,
)


def note(
    note_id: str,
    pitch: int,
    start_tick: int,
    duration_ticks: int = 480,
    velocity: int = 80,
) -> MelodyNoteSnapshot:
    return MelodyNoteSnapshot(note_id, pitch, start_tick, duration_ticks, velocity)


def request(
    melody: tuple[MelodyNoteSnapshot, ...],
    *,
    mode: str = "preview",
    scope: str = "live",
    completed: frozenset[int] = frozenset({0}),
    current_tick: int = 1920,
    tempo: TempoMap = TempoMap(),
    pitch_bounds: tuple[int, int] = (36, 84),
) -> AnalysisRequest:
    return AnalysisRequest(
        project_identity="project-a",
        project_revision=7,
        generation_id=11,
        mode=mode,
        melody=melody,
        tempo=tempo,
        completed_measure_indices=completed,
        current_tick=current_tick,
        min_pitch=pitch_bounds[0],
        max_pitch=pitch_bounds[1],
        scope=scope,
    )


def c_major_melody() -> tuple[MelodyNoteSnapshot, ...]:
    return (
        note("c", 72, 0, 480),
        note("b", 83, 480, 240),
        note("e", 76, 720, 240),
        note("g", 79, 960, 480),
        note("f", 77, 1440, 240),
        note("a", 81, 1680, 120),
        note("c-end", 72, 1800, 120),
    )


@pytest.mark.parametrize(
    ("beats", "unit", "want"), ((4, 4, 1920), (3, 4, 1440), (6, 8, 1440))
)
def test_measure_ticks_follow_real_meter(beats: int, unit: int, want: int) -> None:
    assert measure_ticks(TempoMap(beats_per_bar=beats, beat_unit=unit)) == want


def test_analysis_values_are_immutable_and_slotted() -> None:
    snapshot = note("stable-id", 60, 0)
    assert snapshot.__slots__ == (
        "note_id",
        "pitch",
        "start_tick",
        "duration_ticks",
        "velocity",
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.pitch = 61  # type: ignore[misc]

    for value_type in (
        MelodyNoteSnapshot,
        AnalysisRequest,
        KeyEstimate,
        ScoreBreakdown,
        ChordCandidate,
        MeasureSuggestion,
        HarmonyAnalysisResult,
    ):
        assert value_type.__dataclass_params__.frozen is True
        assert value_type.__slots__ == tuple(field.name for field in fields(value_type))


def test_analysis_result_preserves_the_immutable_request_scope() -> None:
    result = HarmonySuggestionService().analyze(
        request(c_major_melody(), scope="whole_project")
    )

    assert result.scope == "whole_project"


@pytest.mark.parametrize(
    ("tonic", "mode", "intervals"),
    tuple((tonic, "major", (0, 2, 4, 5, 7, 9, 11)) for tonic in range(12))
    + tuple((tonic, "minor", (0, 2, 3, 5, 7, 8, 10)) for tonic in range(12)),
)
def test_key_estimation_compares_all_major_and_natural_minor_keys(
    tonic: int, mode: str, intervals: tuple[int, ...]
) -> None:
    ordered_degrees = (0, 2, 4, 1, 3, 5, 6, 0)
    melody = tuple(
        note(f"n{index}", 60 + ((tonic + intervals[degree]) % 12), index * 240, 240)
        for index, degree in enumerate(ordered_degrees)
    )

    estimate = HarmonySuggestionService().analyze(request(melody)).key_estimate

    assert estimate.tonic_pitch_class == tonic
    assert estimate.mode == mode
    assert 0.0 <= estimate.confidence <= 1.0
    assert estimate.low_confidence is False
    assert estimate.temporary is False


def test_insufficient_c_scale_information_is_temporary_only_in_preview() -> None:
    melody = (note("c", 72, 0, 480), note("e", 76, 480, 240))

    preview = HarmonySuggestionService().analyze(request(melody, mode="preview"))
    automatic = HarmonySuggestionService().analyze(request(melody, mode="auto"))

    assert preview.key_estimate.tonic_pitch_class == 0
    assert preview.key_estimate.mode == "major"
    assert preview.key_estimate.temporary is True
    assert preview.key_estimate.low_confidence is True
    assert automatic.key_estimate.temporary is False
    assert automatic.suggestions == ()


def test_information_outside_c_major_never_uses_temporary_c_major() -> None:
    result = HarmonySuggestionService().analyze(
        request((note("c", 72, 0), note("black-key", 73, 480)), completed=frozenset())
    )

    assert result.key_estimate.temporary is False
    assert result.suggestions == ()


def test_coverage_below_960_ticks_is_low_confidence() -> None:
    melody = (
        note("short-c", 72, 0, 240),
        note("short-e", 76, 240, 240),
        note("short-c-end", 72, 480, 240),
    )

    preview = HarmonySuggestionService().analyze(request(melody, mode="preview"))
    automatic = HarmonySuggestionService().analyze(request(melody, mode="auto"))

    assert preview.key_estimate.low_confidence is True
    assert preview.key_estimate.temporary is True
    assert automatic.suggestions == ()


def test_top_two_key_gap_below_point_twelve_is_low_confidence() -> None:
    melody = tuple(
        note(
            f"chromatic-{pitch_class:02}",
            60 + pitch_class,
            pitch_class * 1920 + 240,
            480,
        )
        for pitch_class in range(12)
    )

    result = HarmonySuggestionService().analyze(
        request(melody, mode="auto", current_tick=23040)
    )

    assert result.key_estimate.confidence < 0.12
    assert result.key_estimate.low_confidence is True
    assert result.key_estimate.temporary is False
    assert result.suggestions == ()


def test_key_confidence_is_stable_when_evidence_distribution_is_repeated() -> None:
    pattern = (
        note("scale-c", 72, 60, 240),
        note("scale-d", 74, 240, 240),
        note("scale-e", 76, 480, 240),
        note("scale-f", 77, 720, 240),
        note("scale-b", 83, 960, 240),
        note("scale-a", 81, 1200, 240),
        note("scale-g", 79, 1680, 240),
    )
    repeated = tuple(
        note(
            f"repeat-{measure}-{item.note_id}",
            item.pitch,
            item.start_tick + measure * 1920,
            item.duration_ticks,
        )
        for measure in range(4)
        for item in pattern
    )

    single = HarmonySuggestionService().analyze(
        request(pattern, mode="auto", completed=frozenset({0}), current_tick=1920)
    ).key_estimate
    scaled = HarmonySuggestionService().analyze(
        request(
            repeated,
            mode="auto",
            completed=frozenset({0, 1, 2, 3}),
            current_tick=7680,
        )
    ).key_estimate

    assert (single.tonic_pitch_class, single.mode) == (0, "major")
    assert (scaled.tonic_pitch_class, scaled.mode) == (0, "major")
    assert single.low_confidence is True
    assert scaled.low_confidence is True
    assert abs(single.confidence - scaled.confidence) <= 0.05


def test_key_estimation_respects_strong_beat_placement() -> None:
    c_on_strong = (
        note("meter-e", 76, 60, 240),
        note("meter-d", 74, 240, 240),
        note("meter-f", 77, 480, 240),
        note("meter-a", 81, 720, 240),
        note("meter-c", 72, 960, 240),
        note("meter-g", 79, 1200, 240),
        note("meter-b", 83, 1680, 240),
    )
    a_on_strong = (
        note("meter-e", 76, 60, 240),
        note("meter-d", 74, 240, 240),
        note("meter-f", 77, 480, 240),
        note("meter-c", 72, 720, 240),
        note("meter-a", 81, 960, 240),
        note("meter-g", 79, 1200, 240),
        note("meter-b", 83, 1680, 240),
    )

    c_estimate = HarmonySuggestionService().analyze(
        request(c_on_strong, mode="auto")
    ).key_estimate
    a_estimate = HarmonySuggestionService().analyze(
        request(a_on_strong, mode="auto")
    ).key_estimate

    assert (c_estimate.tonic_pitch_class, c_estimate.mode) == (0, "major")
    assert (a_estimate.tonic_pitch_class, a_estimate.mode) == (9, "minor")
    assert c_estimate.low_confidence is True
    assert a_estimate.low_confidence is True


def test_c_major_melody_returns_only_stable_major_minor_triads() -> None:
    service = HarmonySuggestionService()

    first_run = service.analyze(request(c_major_melody()))
    second_run = service.analyze(request(c_major_melody()))
    first = first_run.suggestions[0].candidates

    assert 1 <= len(first) <= 3
    assert [candidate.chord_symbol for candidate in first] == ["C", "F", "Am"]
    assert all(candidate.quality in {"major", "minor"} for candidate in first)
    assert all(len({pitch % 12 for pitch in candidate.pitches}) == 3 for candidate in first)
    assert [candidate.score.total for candidate in first] == sorted(
        (candidate.score.total for candidate in first), reverse=True
    )
    assert all(0.0 <= candidate.score.total <= 1.0 for candidate in first)
    assert first == second_run.suggestions[0].candidates


def test_auto_returns_only_explicitly_completed_measures() -> None:
    melody = c_major_melody() + tuple(
        note(
            f"next-{item.note_id}",
            item.pitch,
            item.start_tick + 1920,
            item.duration_ticks,
        )
        for item in c_major_melody()
    )

    result = HarmonySuggestionService().analyze(
        request(melody, mode="auto", completed=frozenset({0}), current_tick=3840)
    )

    assert [item.measure_index for item in result.suggestions] == [0]
    assert result.suggestions[0].temporary is False


def test_preview_marks_uncompleted_tail_temporary() -> None:
    melody = c_major_melody() + tuple(
        note(
            f"next-{item.note_id}",
            item.pitch,
            item.start_tick + 1920,
            item.duration_ticks,
        )
        for item in c_major_melody()
    )

    result = HarmonySuggestionService().analyze(
        request(melody, completed=frozenset({0}), current_tick=3360)
    )

    assert [(item.measure_index, item.temporary) for item in result.suggestions] == [
        (0, False),
        (1, True),
    ]


def test_strong_beat_chord_tone_increases_its_exposed_score() -> None:
    strong = c_major_melody()
    weak = (note("c", 72, 240, 240),) + strong[1:]

    strong_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(strong)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )
    weak_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(weak)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )

    assert strong_c.score.strong_beat_match > weak_c.score.strong_beat_match


def test_sustained_or_ending_chord_tone_increases_its_exposed_score() -> None:
    ending = c_major_melody()
    interior = ending[:-1] + (note("c-end", 72, 1560, 120),)

    ending_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(ending)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )
    interior_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(interior)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )

    assert ending_c.score.sustained_end_match > interior_c.score.sustained_end_match


def test_short_non_chord_tone_adds_only_the_exposed_passing_penalty() -> None:
    plain = c_major_melody()
    ornamented = plain + (note("passing-d", 74, 600, 60, 96),)

    plain_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(plain)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )
    ornamented_c = next(
        item
        for item in HarmonySuggestionService()
        .analyze(request(ornamented))
        .suggestions[0]
        .candidates
        if item.chord_symbol == "C"
    )

    assert plain_c.score.short_non_chord_penalty < ornamented_c.score.short_non_chord_penalty
    assert ornamented_c.score.short_non_chord_penalty <= 0.25


def test_strong_sustained_semitone_conflict_has_a_large_penalty() -> None:
    plain = c_major_melody() + (note("key-evidence-d", 74, 240, 480),)
    conflicted = plain + (note("strong-c-sharp", 73, 960, 480),)

    plain_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(plain)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )
    conflicted_c = next(
        item
        for item in HarmonySuggestionService()
        .analyze(request(conflicted))
        .suggestions[0]
        .candidates
        if item.chord_symbol == "C"
    )

    assert conflicted_c.score.semitone_conflict_penalty >= 0.3
    assert conflicted_c.score.semitone_conflict_penalty > conflicted_c.score.short_non_chord_penalty
    assert conflicted_c.score.total < plain_c.score.total


def test_repeated_chord_exposes_common_tone_reward() -> None:
    first = c_major_melody()
    second = tuple(
        note(f"second-{item.note_id}", item.pitch, item.start_tick + 1920, item.duration_ticks)
        for item in first
    )
    result = HarmonySuggestionService().analyze(
        request(first + second, completed=frozenset({0, 1}), current_tick=3840)
    )
    second_c = next(
        item for item in result.suggestions[1].candidates if item.chord_symbol == "C"
    )

    assert second_c.score.common_tones == 1.0


def test_repeated_voicing_exposes_minimum_voice_movement_reward() -> None:
    first = c_major_melody()
    second = tuple(
        note(f"second-{item.note_id}", item.pitch, item.start_tick + 1920, item.duration_ticks)
        for item in first
    )
    result = HarmonySuggestionService().analyze(
        request(first + second, completed=frozenset({0, 1}), current_tick=3840)
    )
    second_c = next(
        item for item in result.suggestions[1].candidates if item.chord_symbol == "C"
    )

    assert second_c.pitches == (48, 52, 55)
    assert second_c.score.voice_movement == 1.0


def test_register_safety_rewards_chord_below_important_melody() -> None:
    high = c_major_melody()
    low = tuple(
        note(item.note_id, item.pitch - 24, item.start_tick, item.duration_ticks)
        for item in high
    )
    high_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(high)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )
    low_c = next(
        item
        for item in HarmonySuggestionService().analyze(request(low)).suggestions[0].candidates
        if item.chord_symbol == "C"
    )

    assert high_c.score.register_safety > low_c.score.register_safety


@pytest.mark.parametrize(
    "first_measure",
    (
        ((79, 0), (83, 480), (74, 960), (79, 1440)),
        ((77, 0), (81, 480), (72, 960), (77, 1440)),
    ),
)
def test_dominant_or_subdominant_to_tonic_gets_light_cadence_reward(
    first_measure: tuple[tuple[int, int], ...],
) -> None:
    first = tuple(
        note(f"first-{index}", pitch, start, 480)
        for index, (pitch, start) in enumerate(first_measure)
    )
    second_source = c_major_melody()
    second = tuple(
        note(f"second-{item.note_id}", item.pitch, item.start_tick + 1920, item.duration_ticks)
        for item in second_source
    )
    result = HarmonySuggestionService().analyze(
        request(first + second, completed=frozenset({0, 1}), current_tick=3840)
    )
    tonic = next(
        item for item in result.suggestions[1].candidates if item.chord_symbol == "C"
    )

    assert 0.0 < tonic.score.cadence <= 0.12


def test_cadence_reward_cannot_override_strong_semitone_conflict() -> None:
    first = (
        note("g", 79, 0, 480),
        note("b", 83, 480, 480),
        note("d", 74, 960, 480),
        note("g-end", 79, 1440, 480),
        note("key-evidence-e", 76, 240, 480),
        note("key-evidence-f", 77, 1680, 480),
    )
    second = tuple(
        note(f"second-{item.note_id}", item.pitch, item.start_tick + 1920, item.duration_ticks)
        for item in c_major_melody()
    )
    clean = HarmonySuggestionService().analyze(
        request(first + second, completed=frozenset({0, 1}), current_tick=3840)
    )
    conflicted = HarmonySuggestionService().analyze(
        request(
            first + second + (note("conflict", 73, 2880, 480),),
            completed=frozenset({0, 1}),
            current_tick=3840,
        )
    )
    clean_c = next(item for item in clean.suggestions[1].candidates if item.chord_symbol == "C")
    conflicted_c = next(
        item for item in conflicted.suggestions[1].candidates if item.chord_symbol == "C"
    )

    assert conflicted_c.score.semitone_conflict_penalty > conflicted_c.score.cadence
    assert conflicted_c.score.total < clean_c.score.total


def test_non_adjacent_completed_measures_do_not_share_harmonic_context() -> None:
    first = (
        note("gap-g", 79, 0, 480),
        note("gap-b", 83, 480, 480),
        note("gap-d", 74, 960, 480),
        note("gap-g-end", 79, 1440, 480),
    )
    third = tuple(
        note(f"third-{item.note_id}", item.pitch, item.start_tick + 3840, item.duration_ticks)
        for item in c_major_melody()
    )

    result = HarmonySuggestionService().analyze(
        request(
            first + third,
            completed=frozenset({0, 2}),
            current_tick=5760,
        )
    )
    tonic = next(
        item for item in result.suggestions[1].candidates if item.chord_symbol == "C"
    )

    assert tonic.score.cadence == 0.0
    assert tonic.score.common_tones == 0.0
    assert tonic.score.voice_movement == 0.0


def c_major_with_register(
    pitches: tuple[int, int, int, int, int, int, int],
) -> tuple[MelodyNoteSnapshot, ...]:
    starts = (0, 480, 720, 960, 1440, 1680, 1800)
    durations = (480, 240, 240, 480, 240, 120, 120)
    return tuple(
        note(f"register-{index}", pitch, start, duration)
        for index, (pitch, start, duration) in enumerate(
            zip(pitches, starts, durations, strict=True)
        )
    )


@pytest.mark.parametrize(
    ("melody_pitches", "want_voicing", "want_bass_pitch_class"),
        (
            ((48, 59, 52, 55, 53, 57, 60), (36, 40, 43), 0),
            ((60, 59, 64, 55, 53, 57, 60), (40, 43, 48), 4),
            ((60, 59, 64, 67, 65, 69, 60), (43, 48, 52), 7),
        ),
)
def test_voicing_enumerates_root_first_and_second_inversions(
    melody_pitches: tuple[int, ...],
    want_voicing: tuple[int, int, int],
    want_bass_pitch_class: int,
) -> None:
    result = HarmonySuggestionService().analyze(request(c_major_with_register(melody_pitches)))
    tonic = next(item for item in result.suggestions[0].candidates if item.chord_symbol == "C")

    assert tonic.pitches == want_voicing
    assert tonic.pitches[0] % 12 == want_bass_pitch_class
    assert len({pitch % 12 for pitch in tonic.pitches}) == 3


def test_voicing_prefers_five_semitone_safety_before_register_center() -> None:
    melody = c_major_with_register((60, 59, 64, 67, 65, 69, 60))

    result = HarmonySuggestionService().analyze(request(melody))
    tonic = next(item for item in result.suggestions[0].candidates if item.chord_symbol == "C")

    assert tonic.pitches == (43, 48, 52)
    assert min(item.pitch for item in melody if item.duration_ticks >= 240) - max(
        tonic.pitches
    ) >= 5


def test_advanced_bounds_allow_a_lower_octave_placement() -> None:
    melody = c_major_with_register((36, 47, 40, 43, 41, 45, 48))

    result = HarmonySuggestionService().analyze(
        request(melody, pitch_bounds=(12, 108))
    )
    tonic = next(item for item in result.suggestions[0].candidates if item.chord_symbol == "C")

    assert tonic.pitches == (16, 24, 31)
    assert all(12 <= pitch <= 108 for pitch in tonic.pitches)
    assert max(tonic.pitches) <= 36 - 3


def test_beginner_bounds_report_exact_message_when_no_safe_voicing_exists() -> None:
    melody = c_major_with_register((36, 47, 40, 43, 41, 45, 48))

    preview = HarmonySuggestionService().analyze(request(melody, mode="preview"))
    automatic = HarmonySuggestionService().analyze(request(melody, mode="auto"))

    assert preview.suggestions[0].candidates == ()
    assert preview.suggestions[0].message == "当前音区过低，暂未找到清晰的和弦排列"
    assert automatic.suggestions == ()


def velocity_melody(
    *, ornament: bool = False, velocity: int | None = None
) -> tuple[MelodyNoteSnapshot, ...]:
    values = (40, 80, 100, 80) if velocity is None else (velocity,) * 4
    melody = (
        note("velocity-c", 72, 0, 480, values[0]),
        note("velocity-e", 76, 480, 480, values[1]),
        note("velocity-g", 79, 960, 960, values[2]),
        note("velocity-c-end", 72, 1440, 480, values[3]),
    )
    if ornament:
        melody += (note("velocity-ornament", 74, 600, 60, 127),)
    return melody


def test_velocity_uses_duration_and_strong_beat_weighting_with_robust_clamp() -> None:
    result = HarmonySuggestionService().analyze(request(velocity_melody()))
    tonic = next(item for item in result.suggestions[0].candidates if item.chord_symbol == "C")

    # Median 80 clamps 40 to 56. Weighted mean is
    # (56*1.5 + 80*1 + 100*3 + 80*1) / 6.5; * 0.62 rounds to 52.
    assert tonic.velocity == 52


def test_very_short_loud_ornament_does_not_dominate_velocity() -> None:
    plain = HarmonySuggestionService().analyze(request(velocity_melody()))
    ornamented = HarmonySuggestionService().analyze(request(velocity_melody(ornament=True)))
    plain_c = next(item for item in plain.suggestions[0].candidates if item.chord_symbol == "C")
    ornamented_c = next(
        item for item in ornamented.suggestions[0].candidates if item.chord_symbol == "C"
    )

    assert plain_c.velocity == 52
    assert ornamented_c.velocity == 52


@pytest.mark.parametrize(("input_velocity", "want"), ((0, 36), (200, 88)))
def test_velocity_is_clamped_to_supported_output_bounds(input_velocity: int, want: int) -> None:
    result = HarmonySuggestionService().analyze(
        request(velocity_melody(velocity=input_velocity))
    )
    tonic = next(item for item in result.suggestions[0].candidates if item.chord_symbol == "C")

    assert tonic.velocity == want
