"""Bounded, provider-neutral gateway for the fixed music-only tool registry."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap, validate_bpm
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.application import (
    harmony_application_block_reason,
    plan_harmony_candidate_application,
)
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
from ai_piano_studio.harmony.suggestion_service import (
    HarmonySuggestionService,
    measure_ticks,
)
from ai_piano_studio.harmony.types import (
    AnalysisRequest,
    ChordCandidate,
    HarmonyAnalysisResult,
    MeasureSuggestion,
    MelodyNoteSnapshot,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicAgentLimits,
    MusicAgentTurnBudget,
    MusicEditProposal,
    MusicEditProposalStatus,
    MusicExecutionMode,
    MusicPermission,
    MusicToolLimitError,
    MusicToolResult,
    StagedMusicOperation,
)
from ai_piano_studio.music_agent.tool_registry import (
    MusicToolDefinition,
    MusicToolRegistry,
    UnknownMusicToolError,
)


logger = logging.getLogger(__name__)

MAX_COMPACT_CONTEXT_NOTES = 128
MAX_COMPACT_CONTEXT_RANGES = 8
MAX_COMPACT_CONTEXT_BAR_SPAN = 16
MAX_COMPACT_CONTEXT_SOURCE_NOTES = 512
MAX_COMPACT_CONTEXT_HARMONY_MEASURES = 16
MAX_COMPACT_CONTEXT_HARMONY_CANDIDATES = 8
MAX_COMPACT_CONTEXT_STRING_CHARS = 128
_MAX_ABSOLUTE_TOOL_NUMBER = 1_000_000.0
_MAX_NOTE_DURATION_BARS = 64


class MusicToolValidationError(ValueError):
    """Raised for invalid provider payloads before music-domain work begins."""

    def __init__(self, message: str, *, data: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.data = dict(data or {})


class MusicToolPermissionError(PermissionError):
    """Raised when the explicit grant or authorized range does not allow an operation."""


class MusicToolConflictError(RuntimeError):
    """Raised when context or proposal baseline no longer matches the active project."""


class MusicToolNotFoundError(LookupError):
    """Raised when a requested music object no longer exists in the effective state."""

    def __init__(self, message: str, *, data: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.data = dict(data or {})


@dataclass(frozen=True, slots=True)
class HarmonyContextSnapshot:
    """Current Harmony candidates bound to a compact, non-forgeable context token."""

    token: str
    start_bar: int
    end_bar: int
    result: HarmonyAnalysisResult

    def suggestion_for_measure(self, measure_index: int) -> MeasureSuggestion:
        for suggestion in self.result.suggestions:
            if suggestion.measure_index == measure_index:
                return suggestion
        raise MusicToolNotFoundError("harmony measure is not available")

    @staticmethod
    def candidate_at(
        suggestion: MeasureSuggestion, candidate_index: int
    ) -> ChordCandidate:
        if not isinstance(candidate_index, int) or isinstance(candidate_index, bool):
            raise MusicToolValidationError("candidate_index must be an integer")
        try:
            return suggestion.candidates[candidate_index]
        except IndexError as error:
            raise MusicToolNotFoundError("harmony candidate is not available") from error


@dataclass(slots=True)
class _ProposalWorkspace:
    state: ProjectState
    metadata: HarmonyProjectMetadata
    operations: list[StagedMusicOperation]
    sealed: bool = False


@dataclass(frozen=True, slots=True)
class ProposalStageSnapshot:
    """A Gateway-bound baseline and staged state for Phase 4 presentation only."""

    proposal: MusicEditProposal
    baseline_state: ProjectState
    baseline_metadata: HarmonyProjectMetadata
    staged_state: ProjectState
    staged_metadata: HarmonyProjectMetadata


@dataclass(frozen=True, slots=True)
class ProposalReplayStep:
    """One validated staged operation replayed from its original proposal baseline."""

    state: ProjectState
    metadata: HarmonyProjectMetadata


class MusicAgentToolGateway:
    """Validate and execute only registered music tools through controller boundaries."""

    def __init__(
        self,
        controller: EditorController,
        *,
        registry: MusicToolRegistry | None = None,
        harmony_service: HarmonySuggestionService | None = None,
        preserve_note_seconds_on_bpm: bool = False,
    ) -> None:
        if not isinstance(controller, EditorController):
            raise ValueError("controller must be EditorController")
        if registry is not None and not isinstance(registry, MusicToolRegistry):
            raise ValueError("registry must be MusicToolRegistry")
        if harmony_service is not None and not isinstance(
            harmony_service, HarmonySuggestionService
        ):
            raise ValueError("harmony_service must be HarmonySuggestionService")
        self._controller = controller
        self._preserve_note_seconds_on_bpm = preserve_note_seconds_on_bpm
        self._registry = registry or MusicToolRegistry.default()
        self._harmony_service = harmony_service or HarmonySuggestionService()
        self._proposal_workspaces: dict[str, _ProposalWorkspace] = {}
        self._released_proposal_turn_ids: set[str] = set()
        self._active_collaboration_turn: str | None = None
        self._closed_collaboration_turn_ids: set[str] = set()
        self._issued_harmony_contexts: dict[str, HarmonyContextSnapshot] = {}

    @property
    def registry(self) -> MusicToolRegistry:
        return self._registry

    def is_bound_to(self, controller: EditorController) -> bool:
        """Return whether a proposal consumer targets this Gateway's controller."""
        return self._controller is controller

    def create_execution_context(
        self,
        *,
        authorized_bars: tuple[BarRange, ...],
        permissions: frozenset[MusicPermission],
        mode: MusicExecutionMode = MusicExecutionMode.PROPOSAL,
        selected_note_ids: tuple[str, ...] = (),
        intent_template: str = "",
        limits: MusicAgentLimits | None = None,
        append_after_seconds: float | None = None,
    ) -> MusicAgentExecutionContext:
        """Capture the current project/session baseline for one bounded agent turn."""
        if limits is not None and not isinstance(limits, MusicAgentLimits):
            raise ValueError("limits must be MusicAgentLimits")
        return MusicAgentExecutionContext(
            project_identity=self._project_identity(),
            baseline_revision=self._controller.projectRevision,
            authorized_bars=authorized_bars,
            permissions=permissions,
            mode=mode,
            selected_note_ids=selected_note_ids,
            intent_template=intent_template,
            budget=MusicAgentTurnBudget(limits or MusicAgentLimits()),
            append_after_seconds=append_after_seconds,
        )

    def compact_context(
        self,
        context: MusicAgentExecutionContext,
        requested_bars: tuple[BarRange, ...],
    ) -> Mapping[str, object]:
        """Return authorized read-only context facts without consuming a tool budget step."""
        self._require_current_context(context)
        summary: Mapping[str, object] = {}
        notes: list[object] = []
        harmony: list[object] = []
        context_truncated = False
        music_layers: list[dict[str, object]] = []
        active_layer_id = ""
        if MusicPermission.READ_PROJECT_SUMMARY in context.permissions:
            summary = self._get_project_summary(context).data
            state = self._effective_state(context)
            active_layer_id = self._controller.activeLayerId
            note_counts = {
                layer.id: sum(
                    note.layer_id == layer.id for note in state.active_notes
                )
                for layer in state.layers
            }
            music_layers = [
                {
                    "id": layer.id,
                    "name": layer.name,
                    "role": layer.role,
                    "noteCount": note_counts[layer.id],
                    "muted": layer.muted,
                    "solo": layer.solo,
                    "visible": layer.visible,
                    "locked": layer.locked,
                }
                for layer in state.layers
            ]
        for requested_range in requested_bars[:MAX_COMPACT_CONTEXT_RANGES]:
            bar_range = BarRange(
                requested_range.start_bar,
                min(
                    requested_range.end_bar,
                    requested_range.start_bar + MAX_COMPACT_CONTEXT_BAR_SPAN - 1,
                ),
            )
            payload = {"start_bar": bar_range.start_bar, "end_bar": bar_range.end_bar}
            if MusicPermission.READ_NOTES in context.permissions:
                try:
                    compact_notes, truncated = self._compact_notes(
                        payload,
                        context,
                        remaining=MAX_COMPACT_CONTEXT_NOTES - len(notes),
                    )
                    notes.extend(compact_notes)
                    context_truncated = context_truncated or truncated
                except MusicToolPermissionError:
                    pass
            if MusicPermission.READ_HARMONY in context.permissions:
                try:
                    compact_harmony, truncated = self._compact_harmony(
                        payload,
                        context,
                        remaining=MAX_COMPACT_CONTEXT_HARMONY_MEASURES - len(harmony),
                    )
                    harmony.extend(compact_harmony)
                    context_truncated = context_truncated or truncated
                except MusicToolPermissionError:
                    pass
            if (
                len(notes) >= MAX_COMPACT_CONTEXT_NOTES
                and len(harmony) >= MAX_COMPACT_CONTEXT_HARMONY_MEASURES
            ):
                break
        return {
            "summary": summary,
            "nearby_notes": notes,
            "harmony": harmony,
            "musicLayers": music_layers,
            "activeLayerId": active_layer_id,
            "selectionScope": (
                "note"
                if len(context.selected_note_ids) == 1
                else "multi"
                if len(context.selected_note_ids) > 1
                else "project"
            ),
            "context_truncated": context_truncated,
        }

    def current_bar_duration_seconds(self, context: MusicAgentExecutionContext) -> float:
        """Expose the effective tempo needed for boundary-safe normalization."""

        self._require_current_context(context)
        return self._effective_state(context).tempo.bar_duration_seconds

    def _compact_notes(
        self,
        payload: Mapping[str, object],
        context: MusicAgentExecutionContext,
        *,
        remaining: int,
    ) -> tuple[list[dict[str, object]], bool]:
        if remaining <= 0:
            return [], True
        requested = self._requested_bar_range(payload)
        self._require_authorized_range(
            requested, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_READ
        )
        start_seconds, end_seconds = self._bar_span_seconds(requested)
        records: list[dict[str, object]] = []
        examined = 0
        for note in islice(self._controller.state.notes, MAX_COMPACT_CONTEXT_SOURCE_NOTES):
            examined += 1
            if note.status == "deleted":
                continue
            if note.start_time >= end_seconds or note.start_time + note.duration <= start_seconds:
                continue
            records.append(
                {
                    "id": note.id[:MAX_COMPACT_CONTEXT_STRING_CHARS],
                    "pitch": note.pitch,
                    "start_time": note.start_time,
                    "duration": note.duration,
                    "velocity": note.velocity,
                    "layerId": note.layer_id,
                }
            )
            if len(records) >= remaining:
                return records, True
        return records, examined >= MAX_COMPACT_CONTEXT_SOURCE_NOTES

    def _compact_harmony(
        self,
        payload: Mapping[str, object],
        context: MusicAgentExecutionContext,
        *,
        remaining: int,
    ) -> tuple[list[dict[str, object]], bool]:
        if remaining <= 0:
            return [], True
        requested = self._requested_bar_range(payload)
        self._require_authorized_range(
            requested, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_READ
        )
        state, source_truncated = self._compact_harmony_state(requested)
        snapshot = self._current_harmony_context(
            requested,
            state=state,
            metadata=self._controller.harmony_metadata,
        )
        records = [
            {
                "measure_index": suggestion.measure_index,
                "temporary": suggestion.temporary,
                "candidates": [
                    {
                        "candidate_index": index,
                        "chord_symbol": candidate.chord_symbol[:MAX_COMPACT_CONTEXT_STRING_CHARS],
                        "quality": candidate.quality[:MAX_COMPACT_CONTEXT_STRING_CHARS],
                        "score": candidate.score.total,
                    }
                    for index, candidate in enumerate(
                        suggestion.candidates[:MAX_COMPACT_CONTEXT_HARMONY_CANDIDATES]
                    )
                ],
            }
            for suggestion in snapshot.result.suggestions[:remaining]
        ]
        return records, source_truncated or len(records) >= remaining

    def _compact_harmony_state(self, requested: BarRange) -> tuple[ProjectState, bool]:
        start_seconds, end_seconds = self._bar_span_seconds(requested)
        notes: list[Note] = []
        state = self._controller.state
        examined = 0
        for note in islice(state.notes, MAX_COMPACT_CONTEXT_SOURCE_NOTES):
            examined += 1
            if note.status == "deleted":
                continue
            if note.start_time < start_seconds or note.start_time + note.duration > end_seconds:
                continue
            notes.append(note)
            if len(notes) >= MAX_COMPACT_CONTEXT_NOTES:
                return ProjectState(notes=tuple(notes), tempo=state.tempo, layers=state.layers), True
        return (
            ProjectState(notes=tuple(notes), tempo=state.tempo, layers=state.layers),
            examined >= MAX_COMPACT_CONTEXT_SOURCE_NOTES,
        )

    def execute(
        self,
        tool_name: object,
        arguments: object,
        context: MusicAgentExecutionContext,
    ) -> MusicToolResult:
        """Run one allowlisted call through the complete validation pipeline."""
        try:
            definition = self._registry.lookup(tool_name)
        except UnknownMusicToolError:
            self._abort_for_failure(context)
            return MusicToolResult.failure(
                "unregistered", "unknown_tool", "tool is not registered"
            )
        try:
            self._require_current_context(context)
            self._require_permission(definition, context)
            payload = self._validate_schema(definition.input_schema, arguments)
            context.budget.consume_step()
            return self._execute_registered(definition, payload, context)
        except MusicToolPermissionError as error:
            self._abort_for_failure(context)
            return MusicToolResult.failure(
                definition.name, "permission_denied", str(error)
            )
        except MusicToolValidationError as error:
            # Validation is returned to the active Agent Turn so it can decide
            # whether the arguments are locally repairable or deserve one
            # bounded model correction.  Rolling back here closes a
            # Collaboration transaction before that correction can run.
            return MusicToolResult.failure(
                definition.name,
                "validation_error",
                str(error),
                data=error.data,
            )
        except MusicToolLimitError as error:
            self._abort_for_failure(context)
            return MusicToolResult.failure(
                definition.name, "limit_exceeded", str(error)
            )
        except MusicToolNotFoundError as error:
            # A missing note identity can be corrected by the current Agent
            # Turn. Keep proposal/collaboration state alive until the loop
            # decides whether the bounded repair succeeds.
            return MusicToolResult.failure(
                definition.name, "not_found", str(error), data=error.data
            )
        except MusicToolConflictError as error:
            self._abort_for_failure(context)
            return MusicToolResult.failure(definition.name, "conflict", str(error))
        except Exception:
            logger.exception("Music tool %s execution failed", definition.name)
            self._abort_for_failure(context)
            return MusicToolResult.failure(
                definition.name, "execution_error", "music tool execution failed"
            )

    def begin_turn(self, context: MusicAgentExecutionContext) -> None:
        """Start the one controller transaction shared by collaboration tool calls."""
        self._require_current_context(context)
        if context.mode is not MusicExecutionMode.COLLABORATION:
            raise ValueError("collaboration mode is required")
        if self._active_collaboration_turn == context.turn_id:
            return
        if self._active_collaboration_turn is not None:
            raise RuntimeError("another collaboration turn is active")
        self._controller.begin_agent_turn()
        self._active_collaboration_turn = context.turn_id

    def finish_turn(self, context: MusicAgentExecutionContext) -> MusicToolResult:
        """Commit an active collaboration turn once, producing one Undo entry."""
        if not isinstance(context, MusicAgentExecutionContext):
            return MusicToolResult.failure(
                "agent_turn", "validation_error", "execution context is invalid"
            )
        if context.mode is not MusicExecutionMode.COLLABORATION:
            return MusicToolResult.failure(
                "agent_turn", "validation_error", "collaboration mode is required"
            )
        if self._active_collaboration_turn is None:
            if context.turn_id in self._closed_collaboration_turn_ids:
                return MusicToolResult.failure(
                    "agent_turn", "conflict", "collaboration turn is not active"
                )
            return MusicToolResult.success_result("agent_turn", "collaboration turn had no edits")
        if self._active_collaboration_turn != context.turn_id:
            return MusicToolResult.failure(
                "agent_turn", "conflict", "collaboration turn is not active"
            )
        try:
            self._require_current_context(context)
            self._controller.commit_agent_turn()
        except MusicToolConflictError as error:
            self._abort_for_failure(context)
            return MusicToolResult.failure("agent_turn", "conflict", str(error))
        except Exception:
            logger.exception("Music collaboration turn commit failed")
            self._abort_for_failure(context)
            return MusicToolResult.failure(
                "agent_turn", "execution_error", "collaboration turn commit failed"
            )
        self._active_collaboration_turn = None
        self._closed_collaboration_turn_ids.add(context.turn_id)
        return MusicToolResult.success_result("agent_turn", "collaboration turn committed")

    def abort_turn(self, context: MusicAgentExecutionContext) -> MusicToolResult:
        """Explicitly cancel an active collaboration turn without retaining a History edit."""
        if not isinstance(context, MusicAgentExecutionContext):
            return MusicToolResult.failure(
                "agent_turn", "validation_error", "execution context is invalid"
            )
        if self._active_collaboration_turn is None:
            if context.turn_id in self._closed_collaboration_turn_ids:
                return MusicToolResult.failure(
                    "agent_turn", "conflict", "collaboration turn is not active"
                )
            return MusicToolResult.success_result("agent_turn", "collaboration turn had no edits")
        if self._active_collaboration_turn != context.turn_id:
            return MusicToolResult.failure(
                "agent_turn", "conflict", "collaboration turn is not active"
            )
        self._abort_for_failure(context)
        return MusicToolResult.success_result("agent_turn", "collaboration turn cancelled")

    def current_proposal(
        self, context: MusicAgentExecutionContext
    ) -> MusicEditProposal:
        """Return the in-memory proposal for Phase 4 without changing the project."""
        if not isinstance(context, MusicAgentExecutionContext):
            raise ValueError("execution context is invalid")
        workspace = self._proposal_workspace(context)
        return self._proposal_from_workspace(context, workspace)

    def capture_proposal(
        self, context: MusicAgentExecutionContext
    ) -> ProposalStageSnapshot:
        """Capture a current Proposal-mode workspace without exposing a generic write path."""
        self._require_current_context(context)
        if context.mode is not MusicExecutionMode.PROPOSAL:
            raise MusicToolValidationError("proposal mode is required")
        try:
            workspace = self._proposal_workspaces[context.turn_id]
        except KeyError as error:
            raise MusicToolNotFoundError("proposal workspace is not available") from error
        return ProposalStageSnapshot(
            proposal=self._proposal_from_workspace(context, workspace),
            baseline_state=self._controller.state,
            baseline_metadata=self._controller.harmony_metadata,
            staged_state=workspace.state,
            staged_metadata=workspace.metadata,
        )

    def seal_proposal(
        self, context: MusicAgentExecutionContext
    ) -> ProposalStageSnapshot:
        """Freeze a Proposal workspace before exposing it to a preview manager."""
        captured = self.capture_proposal(context)
        workspace = self._proposal_workspaces[context.turn_id]
        if workspace.sealed:
            raise MusicToolConflictError("proposal workspace is already sealed")
        workspace.sealed = True
        return captured

    def iter_replay_proposal(
        self, context: MusicAgentExecutionContext
    ) -> Iterator[ProposalReplayStep]:
        """Yield the original staged operations replayed in their validated order."""
        captured = self.capture_proposal(context)
        state = captured.baseline_state
        metadata = captured.baseline_metadata
        for operation in captured.proposal.operations:
            state, metadata = self._simulate_operation(state, metadata, operation)
            yield ProposalReplayStep(state, metadata)

    def release_proposal(self, context: MusicAgentExecutionContext) -> None:
        """Discard a Proposal-mode workspace so it cannot be replayed or applied later."""
        if not isinstance(context, MusicAgentExecutionContext):
            raise ValueError("execution context is invalid")
        if context.mode is not MusicExecutionMode.PROPOSAL:
            raise ValueError("proposal mode is required")
        self._proposal_workspaces.pop(context.turn_id, None)
        self._released_proposal_turn_ids.add(context.turn_id)

    @staticmethod
    def _proposal_from_workspace(
        context: MusicAgentExecutionContext, workspace: _ProposalWorkspace
    ) -> MusicEditProposal:
        return MusicEditProposal(
            project_identity=context.project_identity,
            baseline_revision=context.baseline_revision,
            authorized_bars=context.authorized_bars,
            operations=tuple(workspace.operations),
            change_summary={
                "added": sum(item.added_notes for item in workspace.operations),
                "updated": sum(item.updated_notes for item in workspace.operations),
                "deleted": sum(item.deleted_notes for item in workspace.operations),
                "bpm": workspace.state.tempo.bpm,
            },
            status=MusicEditProposalStatus.PENDING,
            proposal_id=context.turn_id,
            intent_template=context.intent_template,
        )

    def validate_proposal_baseline(self, proposal: object) -> MusicToolResult:
        """Report whether a Phase 4 apply could safely use the proposal baseline."""
        if not isinstance(proposal, MusicEditProposal):
            return MusicToolResult.failure(
                "proposal", "validation_error", "proposal is invalid"
            )
        if proposal.project_identity != self._project_identity():
            return MusicToolResult.failure(
                "proposal", "conflict", "proposal project session is stale"
            )
        if proposal.baseline_revision != self._controller.projectRevision:
            return MusicToolResult.failure(
                "proposal", "conflict", "proposal project revision is stale"
            )
        return MusicToolResult.success_result("proposal", "proposal baseline is current")

    def _execute_registered(
        self,
        definition: MusicToolDefinition,
        payload: Mapping[str, object],
        context: MusicAgentExecutionContext,
    ) -> MusicToolResult:
        if definition.name == "get_project_summary":
            return self._get_project_summary(context)
        if definition.name == "get_notes":
            return self._get_notes(payload, context)
        if definition.name == "get_harmony_context":
            return self._get_harmony_context(payload, context)
        if definition.name == "add_notes":
            return self._stage_write(self._prepare_add_notes(payload, context), context)
        if definition.name == "update_notes":
            return self._stage_write(self._prepare_update_notes(payload, context), context)
        if definition.name == "delete_notes":
            return self._stage_write(self._prepare_delete_notes(payload, context), context)
        if definition.name == "transpose_notes":
            return self._stage_write(self._prepare_transpose_notes(payload, context), context)
        if definition.name == "set_note_velocity":
            return self._stage_write(self._prepare_set_note_velocity(payload, context), context)
        if definition.name == "set_bpm":
            return self._stage_write(self._prepare_set_bpm(payload), context)
        if definition.name == "apply_chord":
            return self._stage_write(self._prepare_apply_chord(payload, context), context)
        if definition.name == "repeat_section":
            return self._stage_write(self._prepare_repeat_section(payload, context), context)
        if definition.name == "replace_section":
            return self._stage_write(self._prepare_replace_section(payload, context), context)
        return MusicToolResult.failure(
            definition.name, "execution_error", "write tool is not available"
        )

    def _get_project_summary(
        self, context: MusicAgentExecutionContext | None = None
    ) -> MusicToolResult:
        state = self._effective_state(context) if context is not None else self._controller.state
        return MusicToolResult.success_result(
            "get_project_summary",
            "project summary retrieved",
            data={
                "bpm": state.tempo.bpm,
                "beats_per_bar": state.tempo.beats_per_bar,
                "beat_unit": state.tempo.beat_unit,
                "active_note_count": len(state.active_notes),
            },
        )

    def _get_notes(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> MusicToolResult:
        requested = self._requested_bar_range(payload)
        self._require_authorized_range(
            requested, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_READ
        )
        start_seconds, end_seconds = self._bar_span_seconds(requested)
        notes = [
            {
                "id": note.id,
                "pitch": note.pitch,
                "start_time": note.start_time,
                "duration": note.duration,
                "velocity": note.velocity,
            }
            for note in self._effective_state(context).active_notes
            if note.start_time < end_seconds
            and note.start_time + note.duration > start_seconds
        ]
        offset, limit = payload.get('offset', 0), payload.get('limit', 2000)
        total = len(notes)
        page = notes[offset:offset + limit]
        return MusicToolResult.success_result(
            "get_notes",
            f"retrieved {len(notes)} notes overlapping bars {requested.start_bar}-{requested.end_bar}",
            data={
                "start_bar": requested.start_bar,
                "end_bar": requested.end_bar,
                "range_semantics": "overlap",
                "notes": page,
                "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < total else None,
                "total_note_count": total,
            },
        )

    def _get_harmony_context(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> MusicToolResult:
        requested = self._requested_bar_range(payload)
        self._require_authorized_range(
            requested, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_READ
        )
        snapshot = self._current_harmony_context(
            requested,
            state=self._effective_state(context),
            metadata=self._effective_metadata(context),
        )
        self._remember_harmony_context(snapshot)
        measures = [
            {
                "measure_index": suggestion.measure_index,
                "temporary": suggestion.temporary,
                "candidates": [
                    {
                        "candidate_index": index,
                        "chord_symbol": candidate.chord_symbol,
                        "quality": candidate.quality,
                        "score": candidate.score.total,
                    }
                    for index, candidate in enumerate(suggestion.candidates)
                ],
            }
            for suggestion in snapshot.result.suggestions
        ]
        return MusicToolResult.success_result(
            "get_harmony_context",
            f"retrieved {len(measures)} harmony measures",
            data={
                "start_bar": requested.start_bar,
                "end_bar": requested.end_bar,
                "harmony_context_token": snapshot.token,
                "measures": measures,
            },
        )

    def _proposal_workspace(
        self, context: MusicAgentExecutionContext
    ) -> _ProposalWorkspace:
        if context.turn_id in self._released_proposal_turn_ids:
            raise MusicToolConflictError("proposal workspace has been released")
        try:
            return self._proposal_workspaces[context.turn_id]
        except KeyError:
            workspace = _ProposalWorkspace(
                state=self._controller.state,
                metadata=self._controller.harmony_metadata,
                operations=[],
            )
            self._proposal_workspaces[context.turn_id] = workspace
            return workspace

    def _effective_state(self, context: MusicAgentExecutionContext) -> ProjectState:
        if context.mode is MusicExecutionMode.PROPOSAL:
            return self._proposal_workspace(context).state
        return self._controller.state

    def _effective_metadata(
        self, context: MusicAgentExecutionContext
    ) -> HarmonyProjectMetadata:
        if context.mode is MusicExecutionMode.PROPOSAL:
            return self._proposal_workspace(context).metadata
        return self._controller.harmony_metadata

    def _prepare_add_notes(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        raw_notes = payload.get("notes")
        if not isinstance(raw_notes, list):
            raise MusicToolValidationError("notes must be an array")
        state = self._effective_state(context)
        # Report the hard turn budget before softer quality heuristics so an
        # oversized request remains a clear limit failure, not a duplicate note
        # side effect from an earlier staged operation.
        context.budget.check_write(added=len(raw_notes), updated=0, deleted=0)
        existing = state.active_notes
        notes: list[Note] = []
        invalid_items: list[dict[str, object]] = []
        for item_index, item in enumerate(raw_notes):
            try:
                layer_id = (
                    item.get("layer_id", self._controller.activeLayerId)
                    if isinstance(item, Mapping)
                    else self._controller.activeLayerId
                )
                if not isinstance(layer_id, str) or not layer_id:
                    raise MusicToolValidationError("layer_id must be a non-empty string")
                try:
                    state.layer(layer_id)
                except ValueError as error:
                    raise MusicToolValidationError("music layer is not available") from error
                note = self._note_from_payload(item, layer_id=layer_id)
                self._validate_note_quality(note, state, existing)
                self._require_note_write_authorization(note, context, state)
            except MusicToolValidationError as error:
                invalid_items.append(self._validation_item(item_index, error))
                continue
            notes.append(note)
            existing = (*existing, note)
        if invalid_items:
            details = self._invalid_item_summary(invalid_items)
            raise MusicToolValidationError(
                f"{len(invalid_items)} note parameter(s) failed validation; "
                f"{len(notes)} valid item(s) are preserved ({details})",
                data={
                    "invalid_items": invalid_items,
                    "valid_item_count": len(notes),
                    "total_item_count": len(raw_notes),
                    "preserve_valid_items": True,
                },
            )
        notes_tuple = tuple(notes)
        return StagedMusicOperation(
            "add_notes",
            {"notes": notes_tuple},
            added_notes=len(notes_tuple),
            summary=f"staged {len(notes_tuple)} added notes",
        )

    def _prepare_update_notes(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        raw_updates = payload.get("updates")
        if not isinstance(raw_updates, list):
            raise MusicToolValidationError("updates must be an array")
        state = self._effective_state(context)
        updates: list[tuple[str, Mapping[str, object]]] = []
        seen_ids: set[str] = set()
        for raw in raw_updates:
            if not isinstance(raw, Mapping):
                raise MusicToolValidationError("update must be an object")
            note_id = raw.get("id")
            if not isinstance(note_id, str) or not note_id:
                raise MusicToolValidationError("update id must be a non-empty string")
            if note_id in seen_ids:
                raise MusicToolValidationError("updates cannot repeat a note id")
            seen_ids.add(note_id)
            note = self._active_note(state, note_id)
            self._require_note_write_authorization(note, context, state)
            changes = {key: value for key, value in raw.items() if key != "id"}
            if not changes:
                raise MusicToolValidationError("update must change a musical field")
            try:
                changed = note.with_changes(**changes)
            except ValueError as error:
                raise MusicToolValidationError(str(error)) from error
            self._validate_note_quality(
                changed,
                state,
                tuple(candidate for candidate in state.active_notes if candidate.id != note_id),
            )
            self._require_note_write_authorization(changed, context, state)
            updates.append((note_id, changes))
        return StagedMusicOperation(
            "update_notes",
            {"updates": tuple(updates)},
            updated_notes=len(updates),
            summary=f"staged {len(updates)} updated notes",
        )

    def _prepare_delete_notes(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        state = self._effective_state(context)
        note_ids = self._unique_note_ids(payload.get("note_ids"))
        for note_id in note_ids:
            self._require_note_write_authorization(
                self._active_note(state, note_id), context, state
            )
        return StagedMusicOperation(
            "delete_notes",
            {"note_ids": note_ids},
            deleted_notes=len(note_ids),
            summary=f"staged {len(note_ids)} deleted notes",
        )

    def _prepare_transpose_notes(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        state = self._effective_state(context)
        note_ids = self._unique_note_ids(payload.get("note_ids"))
        semitones = payload.get("semitones")
        if not isinstance(semitones, int) or isinstance(semitones, bool):
            raise MusicToolValidationError("semitones must be an integer")
        for note_id in note_ids:
            note = self._active_note(state, note_id)
            self._require_note_write_authorization(note, context, state)
            if not 0 <= note.pitch + semitones <= 127:
                raise MusicToolValidationError("transpose would create an invalid pitch")
        return StagedMusicOperation(
            "transpose_notes",
            {"note_ids": note_ids, "semitones": semitones},
            updated_notes=len(note_ids),
            summary=f"staged transpose for {len(note_ids)} notes",
        )

    def _prepare_set_note_velocity(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        state = self._effective_state(context)
        note_ids = self._unique_note_ids(payload.get("note_ids"))
        velocity = payload.get("velocity")
        if not isinstance(velocity, int) or isinstance(velocity, bool):
            raise MusicToolValidationError("velocity must be an integer")
        for note_id in note_ids:
            self._require_note_write_authorization(
                self._active_note(state, note_id), context, state
            )
        return StagedMusicOperation(
            "set_note_velocity",
            {"note_ids": note_ids, "velocity": velocity},
            updated_notes=len(note_ids),
            summary=f"staged velocity for {len(note_ids)} notes",
        )

    @staticmethod
    def _prepare_set_bpm(payload: Mapping[str, object]) -> StagedMusicOperation:
        try:
            bpm = validate_bpm(payload.get("bpm"))
        except ValueError as error:
            raise MusicToolValidationError(str(error)) from error
        return StagedMusicOperation("set_bpm", {"bpm": bpm}, summary="staged BPM change")

    def _prepare_apply_chord(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        measure_index = payload.get("measure_index")
        candidate_index = payload.get("candidate_index")
        token = payload.get("harmony_context_token")
        if (
            not isinstance(measure_index, int)
            or isinstance(measure_index, bool)
            or not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or not isinstance(token, str)
            or not token
        ):
            raise MusicToolValidationError("harmony candidate reference is invalid")
        measure_bars = BarRange(measure_index + 1, measure_index + 1)
        self._require_authorized_range(
            measure_bars,
            context,
            whole_project_permission=MusicPermission.WHOLE_PROJECT_WRITE,
        )
        issued = self._issued_harmony_contexts.get(token)
        if issued is None or not BarRange(issued.start_bar, issued.end_bar).contains(
            measure_index + 1
        ):
            raise MusicToolConflictError("harmony context is unavailable or does not cover measure")
        state = self._effective_state(context)
        metadata = self._effective_metadata(context)
        current = self._current_harmony_context(
            BarRange(issued.start_bar, issued.end_bar),
            state=state,
            metadata=metadata,
        )
        if current.token != token:
            raise MusicToolConflictError("harmony context is stale")
        suggestion = current.suggestion_for_measure(measure_index)
        candidate = current.candidate_at(suggestion, candidate_index)
        reason = harmony_application_block_reason(
            state,
            metadata,
            suggestion,
            candidate,
            min_pitch=36,
            max_pitch=84,
        )
        if reason is not None:
            raise MusicToolValidationError(
                f"harmony candidate cannot be applied: {reason}"
            )
        context.budget.check_write(
            added=len(candidate.pitches), updated=0, deleted=0
        )
        return StagedMusicOperation(
            "apply_chord",
            {"suggestion": suggestion, "candidate": candidate},
            added_notes=len(candidate.pitches),
            summary=f"staged {candidate.chord_symbol} harmony candidate",
        )

    def _prepare_repeat_section(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        try:
            source = BarRange(
                payload["source_start_bar"], payload["source_end_bar"]
            )
            target = BarRange(
                payload["target_start_bar"],
                payload["target_start_bar"] + source.end_bar - source.start_bar,
            )
        except KeyError as error:
            raise MusicToolValidationError("repeat section range is required") from error
        except ValueError as error:
            raise MusicToolValidationError(str(error)) from error
        self._require_authorized_range(
            source, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_WRITE
        )
        self._require_authorized_range(
            target, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_WRITE
        )
        state = self._effective_state(context)
        source_start, source_end = self._bar_span_seconds(source, state)
        target_start, _target_end = self._bar_span_seconds(target, state)
        source_notes = tuple(
            note
            for note in state.active_notes
            if note.start_time >= source_start
            and note.start_time + note.duration <= source_end
        )
        if not source_notes:
            raise MusicToolNotFoundError("source section has no notes")
        copies = tuple(
            Note.create(
                pitch=note.pitch,
                start_time=note.start_time + target_start - source_start,
                duration=note.duration,
                velocity=note.velocity,
                instrument=note.instrument,
                layer_id=note.layer_id,
            )
            for note in source_notes
        )
        existing = state.active_notes
        for note in copies:
            self._validate_note_quality(note, state, existing)
            existing = (*existing, note)
            self._require_note_write_authorization(note, context, state)
        context.budget.check_write(added=len(copies), updated=0, deleted=0)
        return StagedMusicOperation(
            "repeat_section",
            {"notes": copies, "source": source, "target": target},
            added_notes=len(copies),
            summary=f"staged repeat with {len(copies)} notes",
        )

    def _prepare_replace_section(
        self, payload: Mapping[str, object], context: MusicAgentExecutionContext
    ) -> StagedMusicOperation:
        target = self._requested_bar_range(payload)
        self._require_authorized_range(
            target, context, whole_project_permission=MusicPermission.WHOLE_PROJECT_WRITE
        )
        state = self._effective_state(context)
        start_seconds, end_seconds = self._bar_span_seconds(target, state)
        overlapping = tuple(
            note
            for note in state.active_notes
            if note.start_time < end_seconds and note.start_time + note.duration > start_seconds
        )
        removed = tuple(
            note
            for note in overlapping
            if note.start_time >= start_seconds
            and note.start_time + note.duration <= end_seconds
        )
        if len(removed) != len(overlapping):
            raise MusicToolValidationError(
                "replace target contains a boundary-crossing note"
            )
        for note in removed:
            self._require_note_write_authorization(note, context, state)
        raw_notes = payload.get("notes")
        if not isinstance(raw_notes, list):
            raise MusicToolValidationError("notes must be an array")
        notes = tuple(
            self._note_from_payload(item, layer_id=self._controller.activeLayerId)
            for item in raw_notes
        )
        removed_ids = {note.id for note in removed}
        existing = tuple(candidate for candidate in state.active_notes if candidate.id not in removed_ids)
        for note in notes:
            if (
                note.start_time < start_seconds
                or note.start_time + note.duration > end_seconds
            ):
                raise MusicToolValidationError(
                    "replacement note is outside the target section"
                )
            self._require_note_write_authorization(note, context, state)
            self._validate_note_quality(note, state, existing)
            existing = (*existing, note)
        context.budget.check_write(
            added=len(notes), updated=0, deleted=len(removed)
        )
        return StagedMusicOperation(
            "replace_section",
            {
                "removed_ids": tuple(note.id for note in removed),
                "notes": notes,
                "target": target,
            },
            added_notes=len(notes),
            deleted_notes=len(removed),
            summary=f"staged section replacement with {len(notes)} notes",
        )

    def _stage_write(
        self, operation: StagedMusicOperation, context: MusicAgentExecutionContext
    ) -> MusicToolResult:
        if context.append_after_seconds is not None:
            if operation.name != "add_notes":
                raise MusicToolValidationError(
                    "continuation preserves existing music; only add_notes after the original ending is allowed")
            if any(note.start_time < context.append_after_seconds
                   for note in operation.payload.get("notes", ())):
                raise MusicToolValidationError(
                    f"continuation notes must start at or after {context.append_after_seconds:g} seconds")
        if context.mode is MusicExecutionMode.COLLABORATION:
            return self._preview_collaboration(operation, context)
        if context.mode is not MusicExecutionMode.PROPOSAL:
            raise MusicToolValidationError("execution mode is invalid")
        context.budget.check_write(
            added=operation.added_notes,
            updated=operation.updated_notes,
            deleted=operation.deleted_notes,
        )
        workspace = self._proposal_workspace(context)
        if workspace.sealed:
            raise MusicToolConflictError("proposal workspace is sealed")
        next_state, next_metadata = self._simulate_operation(
            workspace.state, workspace.metadata, operation
        )
        context.budget.consume_write(
            added=operation.added_notes,
            updated=operation.updated_notes,
            deleted=operation.deleted_notes,
        )
        workspace.state = next_state
        workspace.metadata = next_metadata
        workspace.operations.append(operation)
        return MusicToolResult.success_result(
            operation.name,
            operation.summary,
            added_notes=operation.added_notes,
            updated_notes=operation.updated_notes,
            deleted_notes=operation.deleted_notes,
        )

    def _preview_collaboration(
        self, operation: StagedMusicOperation, context: MusicAgentExecutionContext
    ) -> MusicToolResult:
        if self._active_collaboration_turn != context.turn_id:
            raise MusicToolConflictError("collaboration turn is not active")
        context.budget.check_write(
            added=operation.added_notes,
            updated=operation.updated_notes,
            deleted=operation.deleted_notes,
        )
        next_state, next_metadata = self._simulate_operation(
            self._controller.state,
            self._controller.harmony_metadata,
            operation,
        )
        try:
            self._controller.preview_agent_state(
                next_state,
                next_metadata if operation.name == "apply_chord" else None,
            )
        except Exception:
            self._active_collaboration_turn = None
            raise
        context.budget.consume_write(
            added=operation.added_notes,
            updated=operation.updated_notes,
            deleted=operation.deleted_notes,
        )
        return MusicToolResult.success_result(
            operation.name,
            operation.summary,
            added_notes=operation.added_notes,
            updated_notes=operation.updated_notes,
            deleted_notes=operation.deleted_notes,
        )

    def _abort_for_failure(self, context: object) -> None:
        if (
            not isinstance(context, MusicAgentExecutionContext)
            or context.mode is not MusicExecutionMode.COLLABORATION
            or self._active_collaboration_turn != context.turn_id
        ):
            return
        try:
            self._controller.cancel_agent_turn()
        except Exception:
            logger.exception("Music collaboration rollback failed")
        finally:
            self._active_collaboration_turn = None
            self._closed_collaboration_turn_ids.add(context.turn_id)

    def _simulate_operation(
        self,
        state: ProjectState,
        metadata: HarmonyProjectMetadata,
        operation: StagedMusicOperation,
    ) -> tuple[ProjectState, HarmonyProjectMetadata]:
        if operation.name == "apply_chord":
            suggestion = operation.payload.get("suggestion")
            candidate = operation.payload.get("candidate")
            if not isinstance(suggestion, MeasureSuggestion) or not isinstance(
                candidate, ChordCandidate
            ):
                raise RuntimeError("staged harmony operation is invalid")
            return plan_harmony_candidate_application(
                state,
                metadata,
                suggestion,
                candidate,
                origin="preview-manual",
                layer_id=self._controller.activeLayerId,
            )
        if operation.name in {"add_notes", "repeat_section"}:
            notes = operation.payload.get("notes")
            if not isinstance(notes, tuple) or any(not isinstance(note, Note) for note in notes):
                raise RuntimeError("staged add operation is invalid")
            next_state = state
            for note in notes:
                next_state = next_state.add_note(note, select=False)
            return next_state, metadata
        if operation.name == "replace_section":
            removed_ids = operation.payload.get("removed_ids")
            notes = operation.payload.get("notes")
            if (
                not isinstance(removed_ids, tuple)
                or not isinstance(notes, tuple)
                or any(not isinstance(note, Note) for note in notes)
            ):
                raise RuntimeError("staged replace operation is invalid")
            selected = state.selected_ids
            next_state = state.select_ids(removed_ids).delete_selected()
            next_state = next_state.select_ids(selected - frozenset(removed_ids))
            for note in notes:
                next_state = next_state.add_note(note, select=False)
            return next_state, metadata
        if operation.name == "update_notes":
            updates = operation.payload.get("updates")
            if not isinstance(updates, tuple):
                raise RuntimeError("staged update operation is invalid")
            next_state = state
            for note_id, changes in updates:
                next_state = next_state.update_note(note_id, **dict(changes))
            return next_state, metadata
        if operation.name == "delete_notes":
            note_ids = operation.payload.get("note_ids")
            if not isinstance(note_ids, tuple):
                raise RuntimeError("staged delete operation is invalid")
            selected = state.selected_ids
            next_state = state.select_ids(note_ids).delete_selected()
            return next_state.select_ids(selected - frozenset(note_ids)), metadata
        if operation.name == "transpose_notes":
            note_ids = operation.payload.get("note_ids")
            semitones = operation.payload.get("semitones")
            if not isinstance(note_ids, tuple) or not isinstance(semitones, int):
                raise RuntimeError("staged transpose operation is invalid")
            next_state = state
            for note_id in note_ids:
                note = self._active_note(next_state, note_id)
                next_state = next_state.update_note(note_id, pitch=note.pitch + semitones)
            return next_state, metadata
        if operation.name == "set_note_velocity":
            note_ids = operation.payload.get("note_ids")
            velocity = operation.payload.get("velocity")
            if not isinstance(note_ids, tuple) or not isinstance(velocity, int):
                raise RuntimeError("staged velocity operation is invalid")
            next_state = state
            for note_id in note_ids:
                next_state = next_state.update_note(note_id, velocity=velocity)
            return next_state, metadata
        if operation.name == "set_bpm":
            bpm = operation.payload.get("bpm")
            if not isinstance(bpm, int):
                raise RuntimeError("staged BPM operation is invalid")
            tempo = TempoMap(
                bpm=bpm,
                beats_per_bar=state.tempo.beats_per_bar,
                beat_unit=state.tempo.beat_unit,
            )
            return state.with_tempo(
                tempo, preserve_note_seconds=self._preserve_note_seconds_on_bpm
            ), metadata
        raise RuntimeError("staged operation is not supported")

    @staticmethod
    def _note_from_payload(value: object, *, layer_id: str = "layer-default") -> Note:
        if not isinstance(value, Mapping):
            raise MusicToolValidationError("note must be an object")
        try:
            return Note.create(
                pitch=value.get("pitch"),
                start_time=value.get("start_time"),
                duration=value.get("duration"),
                velocity=value.get("velocity"),
                instrument=value.get("instrument", "piano"),
                layer_id=layer_id,
            )
        except ValueError as error:
            raise MusicToolValidationError(str(error)) from error

    @staticmethod
    def _validation_item(item_index: int, error: MusicToolValidationError) -> dict[str, object]:
        raw_items = error.data.get("invalid_items")
        if isinstance(raw_items, list) and raw_items:
            detail = raw_items[0]
            if isinstance(detail, Mapping):
                item: dict[str, object] = {"item_index": item_index}
                for key in (
                    "field",
                    "reason",
                    "start",
                    "duration",
                    "authorized_start",
                    "authorized_end",
                ):
                    value = detail.get(key)
                    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                        item[key] = value
                if "field" in item and "reason" in item:
                    return item
        message = str(error).lower()
        if "duration" in message:
            field = "duration"
            reason = "note_duration_too_long" if "exceed" in message else "invalid_duration"
        elif "duplicate" in message:
            field = "start_time"
            reason = "duplicate_note"
        elif "pitch" in message:
            field = "pitch"
            reason = "invalid_pitch"
        elif "velocity" in message:
            field = "velocity"
            reason = "invalid_velocity"
        elif "start" in message:
            field = "start_time"
            reason = "invalid_start_time"
        else:
            field = "note"
            reason = "invalid_note"
        return {"item_index": item_index, "field": field, "reason": reason}

    @staticmethod
    def _invalid_item_summary(items: Sequence[Mapping[str, object]]) -> str:
        """Render a bounded, safe hint without exposing raw provider payloads."""

        hints: list[str] = []
        for item in items[:3]:
            field = item.get("field")
            reason = item.get("reason")
            if isinstance(field, str) and isinstance(reason, str):
                hints.append(f"{field}: {reason}")
        if len(items) > 3:
            hints.append(f"+{len(items) - 3} more")
        return "; ".join(hints) or "see structured validation details"

    @staticmethod
    def _validate_note_quality(
        note: Note,
        state: ProjectState,
        existing: tuple[Note, ...],
    ) -> None:
        """Reject malformed extremes and exact duplicate starts before staging.

        This is deliberately conservative: it does not cap a composition's
        number of bars or pitch range. It only catches a single malformed note
        that would otherwise make a proposal unusable or silently stack the
        same pitch at the same instant.
        """

        if note.duration > state.tempo.bar_duration_seconds * _MAX_NOTE_DURATION_BARS:
            raise MusicToolValidationError(
                f"duration must not exceed {_MAX_NOTE_DURATION_BARS} musical bars"
            )
        key = (note.pitch, round(note.start_time, 6), note.instrument)
        if any(
            (candidate.pitch, round(candidate.start_time, 6), candidate.instrument) == key
            for candidate in existing
        ):
            raise MusicToolValidationError("duplicate overlapping note at the same pitch and time")

    @staticmethod
    def _unique_note_ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or any(
            not isinstance(note_id, str) or not note_id for note_id in value
        ):
            raise MusicToolValidationError("note_ids must be an array of non-empty strings")
        if len(value) != len(set(value)):
            raise MusicToolValidationError("note_ids cannot contain duplicates")
        return tuple(value)

    @staticmethod
    def _active_note(state: ProjectState, note_id: str) -> Note:
        for note in state.active_notes:
            if note.id == note_id:
                return note
        raise MusicToolNotFoundError(
            "note id is not available",
            data={"invalid_items": [{"field": "id", "reason": "note_id_not_found"}]},
        )

    def _require_note_write_authorization(
        self,
        note: Note,
        context: MusicAgentExecutionContext,
        state: ProjectState,
    ) -> None:
        try:
            layer = state.layer(note.layer_id)
        except ValueError as error:
            raise MusicToolValidationError(
                "music layer is not available",
                data={
                    "invalid_items": [
                        {"field": "layer_id", "reason": "music_layer_not_found"}
                    ]
                },
            ) from error
        if layer.locked:
            raise MusicToolValidationError(
                "music layer is locked",
                data={
                    "invalid_items": [
                        {"field": "layer_id", "reason": "music_layer_locked"}
                    ]
                },
            )
        if MusicPermission.WHOLE_PROJECT_WRITE in context.permissions:
            return
        note_end = note.start_time + note.duration
        for allowed in context.authorized_bars:
            start_seconds, end_seconds = self._bar_span_seconds(allowed, state)
            if note.start_time >= start_seconds and note_end <= end_seconds:
                return
        allowed_ranges = [
            {
                "authorized_start": start_seconds,
                "authorized_end": end_seconds,
            }
            for allowed in context.authorized_bars
            for start_seconds, end_seconds in (self._bar_span_seconds(allowed, state),)
        ]
        detail: dict[str, object] = {
            "field": "start_time",
            "reason": "note_out_of_authorized_range",
            "start": note.start_time,
            "duration": note.duration,
        }
        if len(allowed_ranges) == 1:
            detail.update(allowed_ranges[0])
        raise MusicToolValidationError(
            "note is outside authorized bars",
            data={"invalid_items": [detail]},
        )

    def _project_identity(self) -> str:
        return f"session:{self._controller.projectSessionId}"

    def _require_current_context(self, context: object) -> None:
        if not isinstance(context, MusicAgentExecutionContext):
            raise MusicToolValidationError("execution context is invalid")
        if context.project_identity != self._project_identity():
            raise MusicToolConflictError("project session changed")
        if context.baseline_revision != self._controller.projectRevision:
            raise MusicToolConflictError("project revision changed")

    @staticmethod
    def _require_permission(
        definition: MusicToolDefinition, context: MusicAgentExecutionContext
    ) -> None:
        if definition.permission not in context.permissions:
            raise MusicToolPermissionError("permission is not granted")

    @staticmethod
    def _requested_bar_range(payload: Mapping[str, object]) -> BarRange:
        try:
            return BarRange(payload["start_bar"], payload["end_bar"])
        except KeyError as error:
            raise MusicToolValidationError("bar range is required") from error
        except ValueError as error:
            raise MusicToolValidationError(str(error)) from error

    def _require_authorized_range(
        self,
        requested: BarRange,
        context: MusicAgentExecutionContext,
        *,
        whole_project_permission: MusicPermission,
    ) -> None:
        if whole_project_permission in context.permissions:
            return
        remaining_start = requested.start_bar
        for allowed in sorted(context.authorized_bars, key=lambda item: item.start_bar):
            if allowed.end_bar < remaining_start:
                continue
            if allowed.start_bar > remaining_start:
                break
            remaining_start = max(remaining_start, allowed.end_bar + 1)
            if remaining_start > requested.end_bar:
                return
        raise MusicToolPermissionError("requested bars are not authorized")

    def _bar_span_seconds(
        self, bars: BarRange, state: ProjectState | None = None
    ) -> tuple[float, float]:
        duration = (state or self._controller.state).tempo.bar_duration_seconds
        return (bars.start_bar - 1) * duration, bars.end_bar * duration

    def _remember_harmony_context(self, snapshot: HarmonyContextSnapshot) -> None:
        self._issued_harmony_contexts[snapshot.token] = snapshot
        while len(self._issued_harmony_contexts) > 128:
            oldest = next(iter(self._issued_harmony_contexts))
            del self._issued_harmony_contexts[oldest]

    def _current_harmony_context(
        self,
        requested: BarRange,
        *,
        state: ProjectState | None = None,
        metadata: HarmonyProjectMetadata | None = None,
    ) -> HarmonyContextSnapshot:
        state = self._controller.state if state is None else state
        metadata = self._controller.harmony_metadata if metadata is None else metadata
        tempo = state.tempo
        generated_note_ids = metadata.generated_note_ids
        melody = tuple(
            MelodyNoteSnapshot(
                note.id,
                note.pitch,
                tempo.seconds_to_ticks(note.start_time),
                tempo.seconds_to_ticks(note.duration),
                note.velocity,
            )
            for note in state.active_notes
            if note.id not in generated_note_ids
        )
        ticks_per_measure = measure_ticks(tempo)
        requested_indices = frozenset(
            range(requested.start_bar - 1, requested.end_bar)
        )
        result = self._harmony_service.analyze(
            AnalysisRequest(
                project_identity=self._project_identity(),
                project_revision=self._controller.projectRevision,
                generation_id=0,
                mode="live",
                melody=melody,
                tempo=tempo,
                completed_measure_indices=requested_indices,
                current_tick=requested.end_bar * ticks_per_measure,
                min_pitch=36,
                max_pitch=84,
                scope="live",
            )
        )
        token_payload = {
            "project_identity": result.project_identity,
            "project_revision": result.project_revision,
            "start_bar": requested.start_bar,
            "end_bar": requested.end_bar,
            "tempo": (tempo.bpm, tempo.beats_per_bar, tempo.beat_unit),
            "melody": [
                (item.note_id, item.pitch, item.start_tick, item.duration_ticks, item.velocity)
                for item in melody
            ],
            "candidates": [
                (
                    item.measure_index,
                    tuple(
                        (
                            candidate.chord_symbol,
                            candidate.quality,
                            candidate.diatonic_degree,
                            candidate.pitches,
                            candidate.velocity,
                            candidate.score.total,
                        )
                        for candidate in item.candidates
                    ),
                )
                for item in result.suggestions
            ],
        }
        encoded = json.dumps(
            token_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return HarmonyContextSnapshot(
            token=hashlib.sha256(encoded).hexdigest()[:32],
            start_bar=requested.start_bar,
            end_bar=requested.end_bar,
            result=result,
        )

    def _validate_schema(
        self, schema: Mapping[str, object], value: object, path: str = "arguments"
    ) -> object:
        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(value, Mapping):
                raise MusicToolValidationError(f"{path} must be an object", data={"path": path})
            if any(not isinstance(key, str) for key in value):
                raise MusicToolValidationError(
                    f"{path} has a non-string field", data={"path": path}
                )
            properties = schema.get("properties")
            required = schema.get("required")
            if not isinstance(properties, Mapping) or not isinstance(required, list):
                raise MusicToolValidationError("tool schema is invalid")
            if schema.get("additionalProperties") is False:
                unknown = set(value) - set(properties)
                if unknown:
                    raise MusicToolValidationError(
                        f"{path} contains an unsupported field", data={"path": path}
                    )
            missing = [name for name in required if name not in value]
            if missing:
                raise MusicToolValidationError(
                    f"{path} is missing a required field", data={"path": path}
                )
            minimum_properties = schema.get("minProperties")
            if (
                minimum_properties is not None
                and (not isinstance(minimum_properties, int) or len(value) < minimum_properties)
            ):
                raise MusicToolValidationError(
                    f"{path} has too few fields", data={"path": path}
                )
            return {
                key: self._validate_schema(
                    property_schema,
                    value[key],
                    f"{path}.{key}",
                )
                for key, property_schema in properties.items()
                if key in value and isinstance(property_schema, Mapping)
            }
        if expected_type == "array":
            if not isinstance(value, list):
                raise MusicToolValidationError(f"{path} must be an array", data={"path": path})
            self._validate_size(schema, len(value), path, "Items")
            item_schema = schema.get("items")
            if not isinstance(item_schema, Mapping):
                raise MusicToolValidationError("tool schema is invalid")
            validated_items: list[object] = []
            invalid_items: list[dict[str, object]] = []
            for index, item in enumerate(value):
                try:
                    validated_items.append(
                        self._validate_schema(item_schema, item, f"{path}[{index}]")
                    )
                except MusicToolValidationError as error:
                    invalid_items.append(self._schema_validation_item(path, index, error))
            if invalid_items:
                raise MusicToolValidationError(
                    f"{len(invalid_items)} array item(s) failed schema validation; "
                    f"{len(validated_items)} valid item(s) are preserved",
                    data={
                        "invalid_items": invalid_items,
                        "valid_item_count": len(validated_items),
                        "total_item_count": len(value),
                        "preserve_valid_items": True,
                    },
                )
            return validated_items
        if expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise MusicToolValidationError(
                    f"{path} must be an integer", data={"path": path}
                )
            self._validate_number_bounds(schema, value, path)
            return value
        if expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MusicToolValidationError(f"{path} must be a number")
            numeric = float(value)
            if not math.isfinite(numeric) or abs(numeric) > _MAX_ABSOLUTE_TOOL_NUMBER:
                raise MusicToolValidationError(
                    f"{path} must be finite and within supported bounds", data={"path": path}
                )
            self._validate_number_bounds(schema, numeric, path)
            return numeric
        if expected_type == "string":
            if not isinstance(value, str):
                raise MusicToolValidationError(f"{path} must be a string", data={"path": path})
            self._validate_size(schema, len(value), path, "Length")
            return value
        if expected_type == "boolean":
            if not isinstance(value, bool):
                raise MusicToolValidationError(
                    f"{path} must be a boolean", data={"path": path}
                )
            return value
        raise MusicToolValidationError("tool schema is invalid")

    @staticmethod
    def _schema_validation_item(
        array_path: str, item_index: int, error: MusicToolValidationError
    ) -> dict[str, object]:
        message = str(error).lower()
        detail: dict[str, object] = {"item_index": item_index}
        nested_path = error.data.get("path")
        if not isinstance(nested_path, str) or not nested_path:
            nested_path = ""
        field = nested_path.rsplit(".", 1)[-1] if "." in nested_path else "note"
        if not field or not field.replace("_", "").isalnum():
            field = "note"
        detail["field"] = field
        if "missing a required field" in message:
            reason = "missing_required_field"
        elif "must be an integer" in message:
            reason = "expected_integer"
        elif "must be a number" in message:
            reason = "expected_number"
        elif "above the maximum" in message:
            reason = "value_above_maximum"
        elif "below the minimum" in message:
            reason = "value_below_minimum"
        elif "too short" in message or "too large" in message:
            reason = "value_size_invalid"
        else:
            reason = "invalid_item"
        detail["reason"] = reason
        return detail

    @staticmethod
    def _validate_size(
        schema: Mapping[str, object], size: int, path: str, suffix: str
    ) -> None:
        minimum = schema.get(f"min{suffix}")
        maximum = schema.get(f"max{suffix}")
        if isinstance(minimum, int) and size < minimum:
            raise MusicToolValidationError(f"{path} is too short", data={"path": path})
        if isinstance(maximum, int) and size > maximum:
            raise MusicToolValidationError(f"{path} is too large", data={"path": path})

    @staticmethod
    def _validate_number_bounds(
        schema: Mapping[str, object], value: int | float, path: str
    ) -> None:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise MusicToolValidationError(f"{path} is below the minimum", data={"path": path})
        if isinstance(maximum, (int, float)) and value > maximum:
            raise MusicToolValidationError(f"{path} is above the maximum", data={"path": path})
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            raise MusicToolValidationError(
                f"{path} must be greater than the minimum", data={"path": path}
            )
