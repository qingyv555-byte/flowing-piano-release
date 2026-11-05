"""Pure application planning shared by Harmony UI and bounded music-agent tools."""

from __future__ import annotations

from uuid import uuid4

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)
from ai_piano_studio.harmony.types import ChordCandidate, MeasureSuggestion


_APPLICATION_ORIGINS = frozenset({"auto", "preview-manual", "whole-project"})


def _state_without_generated_ids(
    state: ProjectState, note_ids: frozenset[str]
) -> ProjectState:
    if not note_ids:
        return state
    selected = state.selected_ids
    return state.select_ids(note_ids).delete_selected().select_ids(selected)


def harmony_application_block_reason(
    state: ProjectState,
    metadata: HarmonyProjectMetadata,
    suggestion: MeasureSuggestion,
    candidate: ChordCandidate,
    *,
    min_pitch: int,
    max_pitch: int,
    replacing_measure: bool = False,
) -> str | None:
    """Return the existing Harmony collision/safety reason, if any.

    Staleness, analysis-generation, and automatic-confidence policy belong to
    the caller that owns analysis scheduling. This shared helper owns the
    candidate application safety checks that must be identical for every
    write path.
    """
    pitches = candidate.pitches
    if not (
        len(set(pitches)) == 3
        and all(min_pitch <= pitch <= max_pitch for pitch in pitches)
        and max(pitches) - min(pitches) <= 24
    ):
        return "unsafe_voicing"
    groups = metadata.groups_for_measure(suggestion.measure_index)
    if groups and not replacing_measure:
        return "existing_generated_harmony"
    ignored_ids = (
        frozenset(note_id for group in groups for note_id in group.generated_note_ids)
        if replacing_measure
        else frozenset()
    )
    generated = metadata.generated_note_ids | ignored_ids
    start = state.tempo.ticks_to_seconds(suggestion.start_tick)
    end = state.tempo.ticks_to_seconds(suggestion.start_tick + suggestion.duration_ticks)
    manual_notes = [
        note
        for note in state.active_notes
        if note.id not in generated
        and note.id not in ignored_ids
        and note.start_time < end
        and note.start_time + note.duration > start
    ]
    if any(
        left.pitch != right.pitch
        and max(left.start_time, right.start_time)
        < min(left.start_time + left.duration, right.start_time + right.duration)
        for index, left in enumerate(manual_notes)
        for right in manual_notes[index + 1 :]
    ):
        return "user_written_harmony"
    duration = state.tempo.ticks_to_seconds(suggestion.duration_ticks)
    for note in state.active_notes:
        if note.id in ignored_ids:
            continue
        if any(
            note.pitch == pitch
            and abs(note.start_time - start) < 1e-9
            and abs(note.duration - duration) < 1e-9
            for pitch in pitches
        ):
            return "duplicate_notes"
    return None


def plan_harmony_candidate_application(
    state: ProjectState,
    metadata: HarmonyProjectMetadata,
    suggestion: MeasureSuggestion,
    candidate: ChordCandidate,
    *,
    origin: str,
    replace_measure: bool = False,
    layer_id: str = "layer-default",
) -> tuple[ProjectState, HarmonyProjectMetadata]:
    """Return the current Harmony system's note and metadata transaction target.

    Candidate selection and validation remain callers' responsibility. This helper
    deliberately consumes the existing candidate verbatim, so it cannot create a
    parallel chord, inversion, or voicing policy.
    """
    if not isinstance(state, ProjectState):
        raise ValueError("state must be ProjectState")
    if not isinstance(metadata, HarmonyProjectMetadata):
        raise ValueError("metadata must be HarmonyProjectMetadata")
    if not isinstance(suggestion, MeasureSuggestion):
        raise ValueError("suggestion must be MeasureSuggestion")
    if not isinstance(candidate, ChordCandidate):
        raise ValueError("candidate must be ChordCandidate")
    if origin not in _APPLICATION_ORIGINS:
        raise ValueError("origin is not supported by Harmony metadata")
    if not isinstance(replace_measure, bool):
        raise ValueError("replace_measure must be a boolean")

    next_state = state
    next_metadata = metadata
    if replace_measure:
        old_groups = next_metadata.groups_for_measure(suggestion.measure_index)
        old_ids = frozenset(
            note_id for group in old_groups for note_id in group.generated_note_ids
        )
        next_state = _state_without_generated_ids(next_state, old_ids)
        next_metadata = next_metadata.without_measure(suggestion.measure_index)

    start = next_state.tempo.ticks_to_seconds(suggestion.start_tick)
    duration = next_state.tempo.ticks_to_seconds(suggestion.duration_ticks)
    notes = tuple(
        Note.create(
            id=str(uuid4()),
            pitch=pitch,
            start_time=start,
            duration=duration,
            velocity=candidate.velocity,
            layer_id=layer_id,
        )
        for pitch in candidate.pitches
    )
    for note in notes:
        next_state = next_state.add_note(note, select=False)

    group = HarmonyGroup(
        group_id=str(uuid4()),
        measure_index=suggestion.measure_index,
        start_tick=suggestion.start_tick,
        duration_ticks=suggestion.duration_ticks,
        chord_symbol=candidate.chord_symbol,
        voicing_pitches=candidate.pitches,
        generated_note_ids=tuple(note.id for note in notes),
        original_signatures={note.id: NoteSignature.from_note(note) for note in notes},
        detached=False,
        application_origin=origin,
        replace_policy="automatic" if origin == "auto" else "explicit-only",
    )
    return (
        next_state,
        HarmonyProjectMetadata(
            (*next_metadata.groups, group), next_metadata._unknown_json
        ),
    )
