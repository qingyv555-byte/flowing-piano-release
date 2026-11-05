from __future__ import annotations

from dataclasses import replace
import math

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentLimits,
    MusicEditProposalStatus,
    MusicExecutionMode,
    MusicPermission,
)
from ai_piano_studio.music_agent.tool_gateway import (
    MusicAgentToolGateway,
    MusicToolNotFoundError,
)


def _editor() -> EditorController:
    return EditorController(
        ProjectState(
            notes=(
                Note.create(id="bar-1", pitch=60, start_time=0.0, duration=0.5, velocity=70),
                Note.create(id="bar-2", pitch=64, start_time=2.0, duration=0.5, velocity=80),
                Note.create(id="bar-3", pitch=67, start_time=4.0, duration=0.5, velocity=90),
            )
        )
    )


def _context(gateway: MusicAgentToolGateway, permissions: set[MusicPermission]):
    return gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset(permissions),
    )


def test_get_project_summary_returns_compact_non_path_project_facts() -> None:
    """Break caught: summary exposes full state or filesystem identity to the model."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_PROJECT_SUMMARY})

    result = gateway.execute("get_project_summary", {}, context)

    assert result.success
    assert result.data == {
        "bpm": 120,
        "beats_per_bar": 4,
        "beat_unit": 4,
        "active_note_count": 3,
    }


def test_get_notes_returns_only_compact_notes_inside_authorized_bars() -> None:
    """Break caught: a scoped read leaks notes outside the authorization range."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_NOTES})

    result = gateway.execute("get_notes", {"start_bar": 2, "end_bar": 2}, context)

    assert result.success
    assert result.data == {
        "start_bar": 2,
        "end_bar": 2,
        "range_semantics": "overlap",
        "offset": 0,
        "next_offset": None,
        "total_note_count": 1,
        "notes": [
            {
                "id": "bar-2",
                "pitch": 64,
                "start_time": 2.0,
                "duration": 0.5,
                "velocity": 80,
            }
        ],
    }


def test_get_notes_rejects_range_outside_authorized_bars() -> None:
    """Break caught: READ_NOTES can silently read an unapproved part of the song."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_NOTES})

    result = gateway.execute("get_notes", {"start_bar": 1, "end_bar": 2}, context)

    assert result.success is False
    assert result.error_code == "permission_denied"
    assert result.data == {}


def test_get_notes_requires_read_notes_even_with_an_authorized_range() -> None:
    """Break caught: bar authorization substitutes for the explicit note-read permission."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_PROJECT_SUMMARY})

    result = gateway.execute("get_notes", {"start_bar": 2, "end_bar": 2}, context)

    assert result.success is False
    assert result.error_code == "permission_denied"


def test_add_notes_rejects_absurd_duration_before_staging() -> None:
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.WRITE_NOTES})

    result = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 67, "start_time": 2.0, "duration": 10_000.0, "velocity": 80}]},
        context,
    )

    assert result.success is False
    assert result.error_code == "validation_error"
    assert "duration" in result.summary


def test_add_notes_rejects_exact_pitch_start_duplicates_without_mutating_project() -> None:
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    before = gateway._controller.state

    result = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 64, "start_time": 2.0, "duration": 0.5, "velocity": 80}]},
        context,
    )

    assert result.success is False
    assert result.error_code == "validation_error"
    assert "duplicate" in result.summary
    assert gateway._controller.state == before


def test_locked_layer_write_returns_recoverable_validation_without_state_change() -> None:
    locked = MusicLayer.create(id="locked", name="锁定层", locked=True)
    note = Note.create(
        id="locked-note",
        pitch=60,
        start_time=0.0,
        duration=0.5,
        layer_id="locked",
    )
    editor = EditorController(ProjectState(notes=(note,), layers=(locked,)))
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        selected_note_ids=(note.id,),
    )
    before = editor.state

    result = gateway.execute(
        "update_notes",
        {"updates": [{"id": note.id, "velocity": 90}]},
        context,
    )

    assert result.success is False
    assert result.error_code == "validation_error"
    assert result.data["invalid_items"][0] == {
        "field": "layer_id", "reason": "music_layer_locked"
    }
    assert editor.state == before
    assert gateway.current_proposal(context).operations == ()


@pytest.mark.parametrize(
    "arguments",
    [
        {"start_bar": 2, "end_bar": 2, "unexpected": True},
        {"start_bar": math.nan, "end_bar": 2},
        {"start_bar": math.inf, "end_bar": 2},
        [],
    ],
)
def test_malformed_read_payloads_return_normalized_validation_errors(arguments: object) -> None:
    """Break caught: malformed tool input escapes the gateway or reaches controller state."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_NOTES})

    result = gateway.execute("get_notes", arguments, context)

    assert result.success is False
    assert result.error_code == "validation_error"
    assert result.data.get("path") in {
        "arguments",
        "arguments.start_bar",
    }
    assert set(result.data) == {"path"}


def test_unknown_tool_is_rejected_without_dynamic_dispatch() -> None:
    """Break caught: a provider-supplied arbitrary name reaches Python execution."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_NOTES})

    result = gateway.execute("__import__", {"name": "os"}, context)

    assert result.success is False
    assert result.error_code == "unknown_tool"
    assert result.data == {}


def test_harmony_context_exposes_candidate_indices_but_no_chord_pitches() -> None:
    """Break caught: the model can bypass the Harmony engine by receiving raw voicing data."""
    gateway = MusicAgentToolGateway(_editor())
    context = _context(gateway, {MusicPermission.READ_HARMONY})

    result = gateway.execute(
        "get_harmony_context", {"start_bar": 2, "end_bar": 2}, context
    )

    assert result.success
    assert isinstance(result.data["harmony_context_token"], str)
    assert result.data["harmony_context_token"]
    assert all("pitches" not in candidate for item in result.data["measures"] for candidate in item["candidates"])


def test_proposal_add_and_bpm_leave_project_history_revision_and_playback_unchanged() -> None:
    """Break caught: default agent suggestions directly edit the current project."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}),
    )
    before = (
        editor.state,
        editor.harmony_metadata,
        editor._history.undo_depth,
        editor.projectRevision,
        editor.state.playback_notes,
        editor.state.playback_tempo,
    )

    added = gateway.execute(
        "add_notes",
        {
            "notes": [
                {"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}
            ]
        },
        context,
    )
    bpm = gateway.execute("set_bpm", {"bpm": 100}, context)
    proposal = gateway.current_proposal(context)

    assert added.success and bpm.success
    assert context.mode is MusicExecutionMode.PROPOSAL
    assert (
        editor.state,
        editor.harmony_metadata,
        editor._history.undo_depth,
        editor.projectRevision,
        editor.state.playback_notes,
        editor.state.playback_tempo,
    ) == before
    assert [operation.name for operation in proposal.operations] == ["add_notes", "set_bpm"]
    assert proposal.change_summary == {"added": 1, "updated": 0, "deleted": 0, "bpm": 100}


def test_write_bpm_stages_without_note_read_or_whole_project_permissions() -> None:
    """Break caught: BPM permission grants implicit project/note capabilities."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset({MusicPermission.WRITE_BPM}),
    )

    bpm = gateway.execute("set_bpm", {"bpm": 100}, context)
    notes = gateway.execute("get_notes", {"start_bar": 1, "end_bar": 1}, context)

    assert bpm.success
    assert notes.error_code == "permission_denied"
    assert gateway.current_proposal(context).operations[0].payload == {"bpm": 100}


@pytest.mark.parametrize(
    "payload",
    [
        {"notes": [{"pitch": 128, "start_time": 2.0, "duration": 0.5, "velocity": 75}]},
        {"notes": [{"pitch": 72, "start_time": 2.0, "duration": math.nan, "velocity": 75}]},
        {"notes": [{"id": "model-id", "pitch": 72, "start_time": 2.0, "duration": 0.5, "velocity": 75}]},
    ],
)
def test_invalid_add_payload_never_creates_a_proposal_operation(payload: object) -> None:
    """Break caught: malformed note data can enter a staged edit."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute("add_notes", payload, context)

    assert result.error_code == "validation_error"
    assert gateway.current_proposal(context).operations == ()


def test_add_note_outside_authorized_bars_is_model_repairable_without_staging() -> None:
    """Break caught: WRITE_NOTES crosses the user-approved bar boundary."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 4.0, "duration": 0.5, "velocity": 75}]},
        context,
    )

    assert result.error_code == "validation_error"
    assert result.data["invalid_items"][0]["reason"] == "note_out_of_authorized_range"
    assert result.data["invalid_items"][0]["authorized_start"] == 2.0
    assert result.data["invalid_items"][0]["authorized_end"] == 4.0
    assert gateway.current_proposal(context).operations == ()


def test_update_unknown_note_returns_not_found_without_staging() -> None:
    """Break caught: a missing note id is silently ignored or creates a partial staged write."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "update_notes",
        {"updates": [{"id": "missing", "velocity": 100}]},
        context,
    )

    assert result.error_code == "not_found"
    assert result.data["invalid_items"] == [
        {"field": "id", "reason": "note_id_not_found"}
    ]
    assert gateway.current_proposal(context).operations == ()


def test_collaboration_turn_commits_multiple_writes_as_one_undo() -> None:
    """Break caught: AI collaboration edits create one History entry per tool call."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    before = editor.state

    gateway.begin_turn(context)
    added = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    )
    updated = gateway.execute(
        "set_note_velocity",
        {"note_ids": ["bar-2"], "velocity": 100},
        context,
    )
    bpm = gateway.execute("set_bpm", {"bpm": 100}, context)
    completed = gateway.finish_turn(context)

    assert added.success and updated.success and bpm.success and completed.success
    assert editor._history.undo_depth == 1
    assert editor.state.tempo.bpm == 100
    editor.undo()
    assert editor.state == before


def test_collaboration_finish_rejects_a_stale_turn_context_and_rolls_back() -> None:
    """Break caught: finish commits an active preview even when its context is stale."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth)

    gateway.begin_turn(context)
    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    stale_context = replace(context, baseline_revision=context.baseline_revision + 1)
    completed = gateway.finish_turn(stale_context)

    assert completed.error_code == "conflict"
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth) == before


@pytest.mark.parametrize(
    "tool_name,arguments,expected_error",
    [
        ("set_bpm", {"bpm": 301}, "validation_error"),
        ("shell", {"command": "dir"}, "unknown_tool"),
        ("get_notes", {"start_bar": 1, "end_bar": 1}, "permission_denied"),
    ],
)
def test_collaboration_failure_rolls_back_every_prior_preview(
    tool_name: str, arguments: object, expected_error: str
) -> None:
    """Break caught: a failed later tool leaves earlier AI edits in the project."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision)

    gateway.begin_turn(context)
    first = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    )
    failed = gateway.execute(tool_name, arguments, context)

    assert first.success
    assert failed.error_code == expected_error
    if expected_error == "validation_error":
        assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) != before
        assert gateway.abort_turn(context).success
        assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before
    else:
        assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before
        assert gateway.finish_turn(context).error_code == "conflict"


def test_collaboration_write_requires_an_explicit_turn_boundary() -> None:
    """Break caught: independent tool calls mutate the editor without a shared transaction."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    before = editor.state

    result = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    )

    assert result.error_code == "conflict"
    assert editor.state == before


def test_repeat_section_stages_fresh_note_copies_without_changing_project() -> None:
    """Break caught: repeating a section mutates the live project or reuses note identities."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    before = editor.state

    result = gateway.execute(
        "repeat_section",
        {"source_start_bar": 1, "source_end_bar": 1, "target_start_bar": 2},
        context,
    )
    operation = gateway.current_proposal(context).operations[0]

    assert result.success
    assert editor.state == before
    assert operation.name == "repeat_section"
    copied = operation.payload["notes"]
    assert len(copied) == 1
    assert copied[0].id != "bar-1"
    assert copied[0].start_time == 2.0


def test_replace_section_preflights_all_notes_before_any_delete_is_staged() -> None:
    """Break caught: an invalid replacement can stage removal of the old section first."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "replace_section",
        {
            "start_bar": 2,
            "end_bar": 2,
            "notes": [{"pitch": 200, "start_time": 2.25, "duration": 0.5, "velocity": 75}],
        },
        context,
    )

    assert result.error_code == "validation_error"
    assert gateway.current_proposal(context).operations == ()


def test_replace_section_rejects_new_notes_outside_its_target_even_if_they_are_authorized() -> None:
    """Break caught: replacing one section can insert notes into another authorized section."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "replace_section",
        {
            "start_bar": 2,
            "end_bar": 2,
            "notes": [{"pitch": 72, "start_time": 0.25, "duration": 0.5, "velocity": 75}],
        },
        context,
    )

    assert result.error_code == "validation_error"
    assert gateway.current_proposal(context).operations == ()


def test_collaboration_replace_section_is_atomic_and_undoable_once() -> None:
    """Break caught: replacement deletes prior notes before a later write/undo can restore them."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    before = editor.state

    gateway.begin_turn(context)
    staged = gateway.execute(
        "replace_section",
        {
            "start_bar": 2,
            "end_bar": 2,
            "notes": [{"pitch": 72, "start_time": 2.25, "duration": 0.5, "velocity": 88}],
        },
        context,
    )
    completed = gateway.finish_turn(context)

    assert staged.success and completed.success
    assert [note.pitch for note in editor.state.active_notes if 2.0 <= note.start_time < 4.0] == [72]
    assert editor._history.undo_depth == 1
    editor.undo()
    assert editor.state == before


def test_stale_proposal_baseline_is_reported_without_applying_it() -> None:
    """Break caught: a proposal silently overwrites a project edited after suggestion generation."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    )
    proposal = gateway.current_proposal(context)
    editor._execute(editor.state.with_tempo(editor.state.tempo.__class__(bpm=100)))

    result = gateway.validate_proposal_baseline(proposal)

    assert result.error_code == "conflict"
    assert editor.state.tempo.bpm == 100


def test_whole_project_write_only_extends_authorized_note_range_not_base_write_permission() -> None:
    """Break caught: whole-project range permission substitutes for WRITE_NOTES itself."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WHOLE_PROJECT_WRITE}),
    )

    allowed = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 4.0, "duration": 0.5, "velocity": 75}]},
        context,
    )
    missing_base = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WHOLE_PROJECT_WRITE}),
    )
    denied = gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 4.0, "duration": 0.5, "velocity": 75}]},
        missing_base,
    )

    assert allowed.success
    assert denied.error_code == "permission_denied"


def test_whole_project_read_extends_range_but_does_not_replace_read_notes_permission() -> None:
    """Break caught: WHOLE_PROJECT_READ becomes an implicit broad read capability."""
    gateway = MusicAgentToolGateway(_editor())
    allowed_context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WHOLE_PROJECT_READ}),
    )
    denied_context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WHOLE_PROJECT_READ}),
    )

    allowed = gateway.execute("get_notes", {"start_bar": 1, "end_bar": 1}, allowed_context)
    denied = gateway.execute("get_notes", {"start_bar": 1, "end_bar": 1}, denied_context)

    assert allowed.success
    assert allowed.data["notes"][0]["id"] == "bar-1"
    assert denied.error_code == "permission_denied"


def test_all_basic_note_write_tools_stage_in_order_without_live_mutation() -> None:
    """Break caught: one basic tool lacks staging or observes stale prior proposal operations."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    before = editor.state

    results = (
        gateway.execute("update_notes", {"updates": [{"id": "bar-2", "pitch": 65}]}, context),
        gateway.execute("transpose_notes", {"note_ids": ["bar-2"], "semitones": 2}, context),
        gateway.execute("set_note_velocity", {"note_ids": ["bar-2"], "velocity": 101}, context),
        gateway.execute("delete_notes", {"note_ids": ["bar-2"]}, context),
    )

    assert all(result.success for result in results)
    assert [item.name for item in gateway.current_proposal(context).operations] == [
        "update_notes",
        "transpose_notes",
        "set_note_velocity",
        "delete_notes",
    ]
    assert editor.state == before


def test_gateway_enforces_per_call_and_cumulative_write_limits_before_staging() -> None:
    """Break caught: limits apply only to one payload or only after an operation is stored."""
    gateway = MusicAgentToolGateway(_editor())
    per_call = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        limits=MusicAgentLimits(max_added_notes=1),
    )
    cumulative = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        limits=MusicAgentLimits(max_added_notes=2),
    )
    two_notes = {
        "notes": [
            {"pitch": 72, "start_time": 2.25, "duration": 0.25, "velocity": 75},
            {"pitch": 74, "start_time": 2.75, "duration": 0.25, "velocity": 75},
        ]
    }
    one_note = {"notes": [two_notes["notes"][0]]}

    per_call_result = gateway.execute("add_notes", two_notes, per_call)
    first = gateway.execute("add_notes", one_note, cumulative)
    cumulative_result = gateway.execute("add_notes", two_notes, cumulative)

    assert per_call_result.error_code == "limit_exceeded"
    assert first.success
    assert cumulative_result.error_code == "limit_exceeded"
    assert len(gateway.current_proposal(per_call).operations) == 0
    assert len(gateway.current_proposal(cumulative).operations) == 1


def test_collaboration_limit_failure_rolls_back_prior_preview() -> None:
    """Break caught: a quota failure after one write leaves that first collaboration preview live."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
        limits=MusicAgentLimits(max_added_notes=1),
    )
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth)
    one_note = {"notes": [{"pitch": 72, "start_time": 2.25, "duration": 0.25, "velocity": 75}]}

    gateway.begin_turn(context)
    first = gateway.execute("add_notes", one_note, context)
    second = gateway.execute("add_notes", one_note, context)

    assert first.success
    assert second.error_code == "limit_exceeded"
    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth) == before


def test_gateway_enforces_step_limit_without_consuming_a_thirteenth_registered_call() -> None:
    """Break caught: model retries can exceed the max tool-step bound."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset({MusicPermission.READ_PROJECT_SUMMARY}),
        limits=MusicAgentLimits(max_steps=1),
    )

    first = gateway.execute("get_project_summary", {}, context)
    second = gateway.execute("get_project_summary", {}, context)

    assert first.success
    assert second.error_code == "limit_exceeded"


def test_captured_proposal_replays_its_bound_staged_workspace_without_live_mutation() -> None:
    """Break caught: Phase 4 reads live state or an unrelated staged workspace."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    before = (editor.state, editor.harmony_metadata, editor.projectRevision)

    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    captured = gateway.capture_proposal(context)

    assert captured.proposal.status is MusicEditProposalStatus.PENDING
    assert captured.proposal.operations[0].name == "add_notes"
    assert {note.pitch for note in captured.baseline_state.active_notes} == {60, 64, 67}
    assert {note.pitch for note in captured.staged_state.active_notes} == {60, 64, 67, 72}
    assert (editor.state, editor.harmony_metadata, editor.projectRevision) == before


def test_released_proposal_workspace_cannot_be_captured_or_replayed_again() -> None:
    """Break caught: a discarded proposal retains a valid write/replay handle."""
    gateway = MusicAgentToolGateway(_editor())
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES, MusicPermission.WRITE_BPM}),
    )

    assert gateway.execute(
        "add_notes",
        {"notes": [{"pitch": 72, "start_time": 2.75, "duration": 0.5, "velocity": 96}]},
        context,
    ).success
    assert gateway.execute("set_bpm", {"bpm": 100}, context).success
    steps = tuple(gateway.iter_replay_proposal(context))
    gateway.release_proposal(context)

    assert len(steps) == 2
    assert steps[-1].state.tempo.bpm == 100
    with pytest.raises(MusicToolNotFoundError):
        gateway.capture_proposal(context)
    with pytest.raises(MusicToolNotFoundError):
        tuple(gateway.iter_replay_proposal(context))
