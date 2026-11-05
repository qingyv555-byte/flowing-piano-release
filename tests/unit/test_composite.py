from __future__ import annotations

import math
import random
from time import perf_counter

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.editor.composite import (
    CompositeGroupingConfig,
    CompositeGroupingMode,
    CompositeNoteGrouper,
)


@pytest.mark.parametrize("minimum", [1, 2, 3, 5])
def test_incremental_grouping_matches_fresh_rebuild_through_edits_and_undo(minimum):
    rng = random.Random(731)
    notes = [make_note(str(i), i * .04, 60 + i % 4, .12) for i in range(120)]
    cached = CompositeNoteGrouper()
    config = CompositeGroupingConfig(min_child_count=minimum)
    previous = list(notes)
    for step in range(100):
        mode = "strict" if step % 11 == 0 else "smart"
        expected = CompositeNoteGrouper().group(notes, mode, config)
        assert cached.group(notes, mode, config) == expected
        old = list(notes)
        if step % 4 == 0:
            notes.append(make_note("added-" + str(step), rng.random() * 5, rng.randrange(58, 65), .08))
        elif step % 4 == 1:
            index = rng.randrange(len(notes))
            notes[index] = notes[index].with_changes(start_time=rng.random() * 5, pitch=rng.randrange(58, 65))
        elif step % 4 == 2:
            notes.pop(rng.randrange(len(notes)))
        else:
            notes = previous
        previous = old


def make_note(
    note_id: str,
    start_time: float,
    pitch: int,
    duration: float,
    *,
    instrument: str = "piano",
    layer_id: str = "layer-default",
) -> Note:
    return Note.create(
        id=note_id,
        pitch=pitch,
        start_time=start_time,
        duration=duration,
        instrument=instrument,
        layer_id=layer_id,
    )


def test_strict_groups_adjacent_and_overlapping_same_pitch_without_mutating_notes():
    notes = (
        make_note("a", 1.00, 60, 0.52),
        make_note("b", 1.48, 60, 0.62),
        make_note("c", 2.06, 60, 0.66),
    )
    before = notes

    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.STRICT)

    assert groups[0].child_note_ids == ("a", "b", "c")
    assert (groups[0].start_time, groups[0].end_time) == (1.0, 2.72)
    assert groups[0].trigger_count == 3
    assert groups[0].is_composite is True
    assert notes == before


def test_grouping_never_crosses_instrument_and_smart_does_not_hide_real_melody():
    notes = (
        make_note("f1", 0.0, 65, 0.20),
        make_note("g1", 0.23, 67, 0.20),
        make_note("a1", 0.46, 69, 0.20),
        make_note("other", 0.50, 65, 0.20, instrument="bass"),
    )

    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)

    assert all(set(group.child_note_ids) != {"f1", "g1", "a1"} for group in groups)
    assert all("other" not in group.child_note_ids or len(group.child_note_ids) == 1 for group in groups)


def test_grouping_never_crosses_music_layer_boundary():
    notes = (
        make_note("melody-a", 0.00, 60, 0.20, layer_id="melody"),
        make_note("backing-a", 0.10, 60, 0.20, layer_id="backing"),
        make_note("melody-b", 0.30, 60, 0.20, layer_id="melody"),
    )

    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.STRICT)

    assert [group.child_note_ids for group in groups] == [
        ("melody-a", "melody-b"),
        ("backing-a",),
    ]


def test_smart_can_group_one_semitone_transcription_jitter_but_keeps_pitch_span():
    notes = (
        make_note("a", 0.0, 66, 0.18),
        make_note("b", 0.20, 67, 0.18),
        make_note("c", 0.40, 66, 0.18),
    )

    group = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)[0]

    assert group.child_note_ids == ("a", "b", "c")
    assert (group.pitch_min, group.pitch_max) == (66, 67)


def test_singletons_are_emitted_as_non_composite_items():
    item = CompositeNoteGrouper().group((make_note("solo", 0.0, 60, 1.0),))[0]

    assert item.child_note_ids == ("solo",)
    assert item.is_composite is False


def test_smart_does_not_merge_long_near_pitch_notes():
    notes = (
        make_note("first", 0.0, 66, 0.5),
        make_note("second", 0.51, 67, 0.5),
    )

    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)

    assert [group.child_note_ids for group in groups] == [("first",), ("second",)]


def test_smart_does_not_hide_long_note_after_pitch_jitter():
    notes = (
        make_note("short-a", 0.0, 66, 0.18),
        make_note("short-b", 0.20, 67, 0.18),
        make_note("long-b", 0.40, 67, 0.50),
    )

    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)

    assert [group.child_note_ids for group in groups] == [
        ("short-a", "short-b"),
        ("long-b",),
    ]


def test_grouping_is_deterministic_and_omits_deleted_notes():
    notes = (
        make_note("z", 0.0, 60, 0.2, instrument="piano"),
        make_note("a", 0.0, 60, 0.2, instrument="bass"),
        make_note("deleted", 0.1, 60, 0.2).with_changes(status="deleted"),
        make_note("b", 0.2, 60, 0.2, instrument="bass"),
    )

    first = CompositeNoteGrouper().group(notes, CompositeGroupingMode.STRICT)
    second = CompositeNoteGrouper().group(reversed(notes), CompositeGroupingMode.STRICT)

    assert first == second
    assert all("deleted" not in group.child_note_ids for group in first)
    assert [group.child_note_ids for group in first] == [("a", "b"), ("z",)]


@pytest.mark.parametrize(
    "field,value",
    [
        ("same_pitch_gap_seconds", math.nan),
        ("near_pitch_gap_seconds", math.inf),
        ("max_group_duration_seconds", -1.0),
        ("max_pitch_delta_semitones", True),
        ("min_child_count", 0),
    ],
)
def test_grouping_config_rejects_invalid_thresholds(field: str, value: object):
    values = {
        "same_pitch_gap_seconds": 0.150,
        "near_pitch_gap_seconds": 0.080,
        "max_pitch_delta_semitones": 1,
        "min_child_count": 2,
        "max_group_duration_seconds": 8.0,
        "overlap_threshold_seconds": 0.0,
        "micro_fragment_seconds": 0.320,
    }
    values[field] = value

    with pytest.raises(ValueError):
        CompositeGroupingConfig(**values)


@pytest.mark.parametrize("note_count", [500, 1000, 5000])
def test_grouping_large_inputs_reports_a_measurement_without_a_machine_limit(note_count: int, capsys):
    notes = tuple(
        make_note(f"n{i}", i * 0.2, 60 + (i % 2), 0.18)
        for i in range(note_count)
    )

    started = perf_counter()
    groups = CompositeNoteGrouper().group(notes, CompositeGroupingMode.SMART)
    elapsed = perf_counter() - started
    print(f"composite grouping {note_count} notes: {elapsed:.6f}s, {len(groups)} items")

    assert groups
    assert math.isfinite(elapsed)
    assert capsys.readouterr().out.startswith("composite grouping")
