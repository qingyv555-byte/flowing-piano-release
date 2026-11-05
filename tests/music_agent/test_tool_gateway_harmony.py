from __future__ import annotations

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicExecutionMode,
    MusicPermission,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway


def _editor_with_stable_harmony_candidates() -> EditorController:
    melody = (
        (72, 0.0, 0.5),
        (83, 0.5, 0.25),
        (76, 0.75, 0.25),
        (79, 1.0, 0.5),
        (77, 1.5, 0.25),
        (81, 1.75, 0.125),
        (72, 1.875, 0.125),
    )
    return EditorController(
        ProjectState(
            notes=tuple(
                Note.create(
                    id=f"melody-{index}",
                    pitch=pitch,
                    start_time=start_time,
                    duration=duration,
                    velocity=80,
                )
                for index, (pitch, start_time, duration) in enumerate(melody)
            )
        )
    )


def _context(
    gateway: MusicAgentToolGateway, *, mode: MusicExecutionMode
):
    return gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_HARMONY, MusicPermission.WRITE_HARMONY}
        ),
        mode=mode,
    )


def _candidate_reference(gateway: MusicAgentToolGateway, context) -> dict[str, object]:
    result = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )
    assert result.success
    assert result.data["measures"] and result.data["measures"][0]["candidates"]
    return {
        "measure_index": result.data["measures"][0]["measure_index"],
        "candidate_index": result.data["measures"][0]["candidates"][0]["candidate_index"],
        "harmony_context_token": result.data["harmony_context_token"],
    }


def test_collaboration_apply_chord_reuses_current_harmony_application_transaction() -> None:
    """Break caught: AI applies chord notes/metadata outside the existing Harmony transaction."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, mode=MusicExecutionMode.COLLABORATION)
    arguments = _candidate_reference(gateway, context)
    before = (editor.state, editor.harmony_metadata)

    gateway.begin_turn(context)
    applied = gateway.execute("apply_chord", arguments, context)
    completed = gateway.finish_turn(context)

    assert applied.success and completed.success
    assert len(editor.harmony_metadata.groups) == 1
    group = editor.harmony_metadata.groups[0]
    assert {note.id for note in editor.state.active_notes if note.id in group.generated_note_ids} == set(group.generated_note_ids)
    assert editor._history.undo_depth == 1
    editor.undo()
    assert (editor.state, editor.harmony_metadata) == before


def test_proposal_apply_chord_stages_existing_candidate_without_live_harmony_mutation() -> None:
    """Break caught: default Harmony tool application bypasses Proposal Mode."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, mode=MusicExecutionMode.PROPOSAL)
    arguments = _candidate_reference(gateway, context)
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth)

    applied = gateway.execute("apply_chord", arguments, context)
    proposal = gateway.current_proposal(context)

    assert applied.success
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth) == before
    assert [operation.name for operation in proposal.operations] == ["apply_chord"]
    assert proposal.change_summary["added"] == 3


def test_proposal_harmony_context_is_bound_to_the_staged_bpm_snapshot() -> None:
    """Break caught: Harmony analysis ignores an earlier BPM change staged in the proposal."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset(
            {MusicPermission.READ_HARMONY, MusicPermission.WRITE_BPM}
        ),
        mode=MusicExecutionMode.PROPOSAL,
    )

    before = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )
    assert before.success
    assert gateway.execute("set_bpm", {"bpm": 100}, context).success
    after = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )

    assert after.success
    assert after.data["harmony_context_token"] != before.data["harmony_context_token"]
    assert editor.state.tempo.bpm == 120


def test_apply_chord_rejects_a_measure_with_existing_generated_harmony() -> None:
    """Break caught: the AI Harmony path bypasses the shared collision safety gate."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    initial_context = _context(gateway, mode=MusicExecutionMode.COLLABORATION)
    initial_arguments = _candidate_reference(gateway, initial_context)

    gateway.begin_turn(initial_context)
    assert gateway.execute("apply_chord", initial_arguments, initial_context).success
    assert gateway.finish_turn(initial_context).success
    before_retry = (editor.state, editor.harmony_metadata, editor._history.undo_depth)

    retry_context = _context(gateway, mode=MusicExecutionMode.COLLABORATION)
    retry_arguments = _candidate_reference(gateway, retry_context)
    gateway.begin_turn(retry_context)
    rejected = gateway.execute("apply_chord", retry_arguments, retry_context)

    assert rejected.error_code == "validation_error"
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth) == before_retry


def test_apply_chord_rejects_an_expired_harmony_context_before_mutating() -> None:
    """Break caught: a changed candidate list lets an old index select a different chord."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    original_context = _context(gateway, mode=MusicExecutionMode.PROPOSAL)
    arguments = _candidate_reference(gateway, original_context)
    editor._execute(
        editor.state.add_note(
            Note.create(id="intervening-edit", pitch=60, start_time=0.25, duration=0.25),
            select=False,
        )
    )
    current_context = _context(gateway, mode=MusicExecutionMode.COLLABORATION)
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth)

    gateway.begin_turn(current_context)
    result = gateway.execute("apply_chord", arguments, current_context)

    assert result.error_code == "conflict"
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth) == before


@pytest.mark.parametrize("field", ["pitches", "notes", "voicing", "inversion", "chord_tones"])
def test_apply_chord_schema_rejects_model_supplied_chord_construction(field: str) -> None:
    """Break caught: the model can bypass Harmony voicing by supplying its own chord notes."""
    gateway = MusicAgentToolGateway(_editor_with_stable_harmony_candidates())
    context = _context(gateway, mode=MusicExecutionMode.PROPOSAL)

    result = gateway.execute(
        "apply_chord",
        {
            "measure_index": 0,
            "candidate_index": 0,
            "harmony_context_token": "not-used-after-schema-rejection",
            field: [60, 64, 67],
        },
        context,
    )

    assert result.error_code == "validation_error"


def test_apply_chord_candidate_index_is_revalidated_against_current_context() -> None:
    """Break caught: an absent candidate index produces partial harmony writes."""
    editor = _editor_with_stable_harmony_candidates()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, mode=MusicExecutionMode.COLLABORATION)
    arguments = _candidate_reference(gateway, context)
    arguments["candidate_index"] = 99
    before = (editor.state, editor.harmony_metadata)

    gateway.begin_turn(context)
    result = gateway.execute("apply_chord", arguments, context)

    assert result.error_code == "not_found"
    assert (editor.state, editor.harmony_metadata) == before
