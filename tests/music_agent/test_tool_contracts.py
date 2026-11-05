from __future__ import annotations

import pytest

from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentLimits,
    MusicAgentTurnBudget,
    MusicAgentExecutionContext,
    MusicEditProposal,
    MusicExecutionMode,
    MusicPermission,
    MusicToolLimitError,
    MusicToolResult,
    StagedMusicOperation,
)


@pytest.mark.parametrize("boundary", [-1, float("nan"), float("inf"), True, "10"])
def test_continuation_boundary_rejects_invalid_values(boundary):
    with pytest.raises(ValueError, match="append_after_seconds"):
        MusicAgentExecutionContext(project_identity="fixture", baseline_revision=0,
            authorized_bars=(BarRange(1, 8),), permissions=frozenset(),
            append_after_seconds=boundary)


def test_write_bpm_is_independent_of_note_and_whole_project_permissions() -> None:
    """Break caught: BPM authorization accidentally grants project editing rights."""
    permissions = frozenset({MusicPermission.WRITE_BPM})

    assert MusicPermission.WRITE_BPM in permissions
    assert MusicPermission.WRITE_NOTES not in permissions
    assert MusicPermission.WRITE_HARMONY not in permissions
    assert MusicPermission.WHOLE_PROJECT_WRITE not in permissions


def test_default_limits_do_not_cap_a_user_requested_music_turn() -> None:
    """A normal assistant turn must not stop solely because it is large."""
    limits = MusicAgentLimits()

    assert (
        limits.max_steps,
        limits.max_write_calls,
        limits.max_added_notes,
        limits.max_updated_notes,
        limits.max_deleted_notes,
    ) == (None, None, None, None, None)


def test_default_budget_accepts_large_turns_without_a_safety_cap() -> None:
    budget = MusicAgentTurnBudget(MusicAgentLimits())

    for _ in range(20):
        budget.consume_step()
    for _ in range(12):
        budget.consume_write(added=0, updated=0, deleted=0)
    budget.consume_write(added=2_500, updated=3_000, deleted=2_500)

    assert budget.steps == 20
    assert budget.write_calls == 13
    assert budget.added_notes == 2_500
    assert budget.updated_notes == 3_000
    assert budget.deleted_notes == 2_500


def test_bar_range_is_one_based_and_inclusive() -> None:
    """Break caught: a range admits bar zero or silently swaps the requested bounds."""
    allowed = BarRange(4, 8)

    assert allowed.contains(4)
    assert allowed.contains(8)
    assert not allowed.contains(3)
    assert not allowed.contains(9)
    with pytest.raises(ValueError, match="start_bar"):
        BarRange(0, 1)
    with pytest.raises(ValueError, match="end_bar"):
        BarRange(5, 4)


def test_budget_rejects_a_ninth_write_before_it_is_recorded() -> None:
    """Break caught: an agent can issue more than eight writing calls in one turn."""
    budget = MusicAgentTurnBudget(MusicAgentLimits(max_write_calls=8))
    for _ in range(8):
        budget.consume_write(added=0, updated=0, deleted=0)

    with pytest.raises(MusicToolLimitError, match="write"):
        budget.consume_write(added=0, updated=0, deleted=0)

    assert budget.write_calls == 8


def test_budget_rejects_cumulative_additions_above_2000() -> None:
    """Break caught: individually valid writes bypass the overall note-addition cap."""
    budget = MusicAgentTurnBudget(MusicAgentLimits(max_added_notes=2_000))
    budget.consume_write(added=1500, updated=0, deleted=0)

    with pytest.raises(MusicToolLimitError, match="added"):
        budget.consume_write(added=501, updated=0, deleted=0)

    assert budget.added_notes == 1500


def test_budget_rejects_a_thirteenth_step_before_counter_changes() -> None:
    """Break caught: tool processing can exceed the turn's total call budget."""
    budget = MusicAgentTurnBudget(MusicAgentLimits(max_steps=12))
    for _ in range(12):
        budget.consume_step()

    with pytest.raises(MusicToolLimitError, match="step"):
        budget.consume_step()

    assert budget.steps == 12


def test_proposal_keeps_baseline_identity_range_operations_and_summary() -> None:
    """Break caught: Phase 4 lacks the baseline data needed to reject stale suggestions."""
    proposal = MusicEditProposal(
        project_identity="project-7",
        baseline_revision=3,
        authorized_bars=(BarRange(2, 4),),
        operations=(
            StagedMusicOperation(
                name="set_bpm",
                payload={"bpm": 100},
                added_notes=0,
                updated_notes=0,
                deleted_notes=0,
            ),
        ),
        change_summary={"added": 0, "updated": 0, "deleted": 0, "bpm": 100},
    )

    assert proposal.project_identity == "project-7"
    assert proposal.baseline_revision == 3
    assert proposal.authorized_bars == (BarRange(2, 4),)
    assert proposal.operations[0].name == "set_bpm"
    assert proposal.change_summary["bpm"] == 100


def test_result_failure_exposes_a_stable_compact_error_only() -> None:
    """Break caught: internal exception/state detail can leak through a tool failure."""
    result = MusicToolResult.failure(
        tool_name="add_notes",
        error_code="validation_error",
        summary="pitch must be an integer from 0 to 127",
    )

    assert result.success is False
    assert result.error_code == "validation_error"
    assert result.data == {}
    assert result.added_notes == result.updated_notes == result.deleted_notes == 0
    assert MusicExecutionMode.PROPOSAL.value == "proposal"


def test_public_music_agent_api_exports_the_phase_three_contracts() -> None:
    """Break caught: consumers cannot import the supported tool contract surface."""
    from ai_piano_studio.music_agent import (
        MusicAgentExecutionContext as PublicContext,
        MusicExecutionMode as PublicMode,
        MusicPermission as PublicPermission,
    )

    assert PublicMode.PROPOSAL.value == "proposal"
    assert PublicPermission.WRITE_BPM.value == "write_bpm"
    assert PublicContext.__name__ == "MusicAgentExecutionContext"
