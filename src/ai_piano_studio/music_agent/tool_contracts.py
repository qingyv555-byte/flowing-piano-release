"""Immutable contracts for music-agent tool execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import math
from types import MappingProxyType
from uuid import uuid4


_ERROR_CODES = frozenset(
    {
        "unknown_tool",
        "permission_denied",
        "source_confirmation_required",
        "resource_unavailable",
        "validation_error",
        "limit_exceeded",
        "not_found",
        "conflict",
        "execution_error",
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _frozen_mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be a mapping with string keys")
    return MappingProxyType(dict(value))


class MusicPermission(StrEnum):
    """Flat, least-privilege permissions for one music-agent turn."""

    READ_PROJECT_SUMMARY = "read_project_summary"
    READ_PUBLIC_MUSIC_RESOURCES = "read_public_music_resources"
    MANAGE_APP_APPEARANCE = "manage_app_appearance"
    READ_NOTES = "read_notes"
    READ_HARMONY = "read_harmony"
    WRITE_NOTES = "write_notes"
    WRITE_BPM = "write_bpm"
    WRITE_HARMONY = "write_harmony"
    WHOLE_PROJECT_READ = "whole_project_read"
    WHOLE_PROJECT_WRITE = "whole_project_write"


class MusicExecutionMode(StrEnum):
    """Controls whether write operations stage or use the controller transaction."""

    PROPOSAL = "proposal"
    COLLABORATION = "collaboration"


class MusicEditProposalStatus(StrEnum):
    """Terminal-safe lifecycle state for an in-memory music suggestion."""

    PENDING = "pending"
    APPLIED = "applied"
    DISCARDED = "discarded"
    STALE = "stale"
    FAILED = "failed"


class MusicToolLimitError(ValueError):
    """Raised before a tool call would exceed an explicitly configured turn limit."""


@dataclass(frozen=True, slots=True)
class BarRange:
    """A one-based, inclusive musical bar range."""

    start_bar: int
    end_bar: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start_bar, int)
            or isinstance(self.start_bar, bool)
            or self.start_bar < 1
        ):
            raise ValueError("start_bar must be a positive integer")
        if (
            not isinstance(self.end_bar, int)
            or isinstance(self.end_bar, bool)
            or self.end_bar < self.start_bar
        ):
            raise ValueError("end_bar must be an integer greater than or equal to start_bar")

    def contains(self, bar: object) -> bool:
        return isinstance(bar, int) and not isinstance(bar, bool) and self.start_bar <= bar <= self.end_bar


@dataclass(frozen=True, slots=True)
class MusicAgentLimits:
    """Optional per-call and cumulative limits; ``None`` means no application cap."""

    max_steps: int | None = None
    max_write_calls: int | None = None
    max_added_notes: int | None = None
    max_updated_notes: int | None = None
    max_deleted_notes: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "max_steps",
            "max_write_calls",
            "max_added_notes",
            "max_updated_notes",
            "max_deleted_notes",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field_name} must be a positive integer or None")


@dataclass(slots=True)
class MusicAgentTurnBudget:
    """Mutable counters scoped to exactly one execution context/agent turn."""

    limits: MusicAgentLimits = field(default_factory=MusicAgentLimits)
    steps: int = 0
    write_calls: int = 0
    added_notes: int = 0
    updated_notes: int = 0
    deleted_notes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.limits, MusicAgentLimits):
            raise ValueError("limits must be MusicAgentLimits")
        for field_name in (
            "steps",
            "write_calls",
            "added_notes",
            "updated_notes",
            "deleted_notes",
        ):
            _require_non_negative_int(getattr(self, field_name), field_name)

    def consume_step(self) -> None:
        if self.limits.max_steps is not None and self.steps + 1 > self.limits.max_steps:
            raise MusicToolLimitError("agent step limit exceeded")
        self.steps += 1

    def check_write(self, *, added: object, updated: object, deleted: object) -> None:
        additions = _require_non_negative_int(added, "added")
        updates = _require_non_negative_int(updated, "updated")
        deletions = _require_non_negative_int(deleted, "deleted")
        if (
            self.limits.max_write_calls is not None
            and self.write_calls + 1 > self.limits.max_write_calls
        ):
            raise MusicToolLimitError("write call limit exceeded")
        if self.limits.max_added_notes is not None and (
            additions > self.limits.max_added_notes
            or self.added_notes + additions > self.limits.max_added_notes
        ):
            raise MusicToolLimitError("added note limit exceeded")
        if self.limits.max_updated_notes is not None and (
            updates > self.limits.max_updated_notes
            or self.updated_notes + updates > self.limits.max_updated_notes
        ):
            raise MusicToolLimitError("updated note limit exceeded")
        if self.limits.max_deleted_notes is not None and (
            deletions > self.limits.max_deleted_notes
            or self.deleted_notes + deletions > self.limits.max_deleted_notes
        ):
            raise MusicToolLimitError("deleted note limit exceeded")

    def consume_write(self, *, added: object, updated: object, deleted: object) -> None:
        self.check_write(added=added, updated=updated, deleted=deleted)
        self.write_calls += 1
        self.added_notes += int(added)
        self.updated_notes += int(updated)
        self.deleted_notes += int(deleted)


@dataclass(frozen=True, slots=True)
class MusicAgentExecutionContext:
    """Non-sensitive authorization and baseline data supplied for one agent turn."""

    project_identity: str
    baseline_revision: int
    authorized_bars: tuple[BarRange, ...]
    permissions: frozenset[MusicPermission]
    mode: MusicExecutionMode = MusicExecutionMode.PROPOSAL
    turn_id: str = field(default_factory=lambda: str(uuid4()))
    selected_note_ids: tuple[str, ...] = ()
    intent_template: str = ""
    budget: MusicAgentTurnBudget = field(default_factory=MusicAgentTurnBudget, compare=False)
    append_after_seconds: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.project_identity, "project_identity")
        _require_non_negative_int(self.baseline_revision, "baseline_revision")
        _require_text(self.turn_id, "turn_id")
        if not isinstance(self.authorized_bars, tuple) or any(
            not isinstance(item, BarRange) for item in self.authorized_bars
        ):
            raise ValueError("authorized_bars must be a tuple of BarRange")
        if not isinstance(self.permissions, frozenset) or any(
            not isinstance(item, MusicPermission) for item in self.permissions
        ):
            raise ValueError("permissions must be a frozenset of MusicPermission")
        if not isinstance(self.mode, MusicExecutionMode):
            raise ValueError("mode must be MusicExecutionMode")
        if not isinstance(self.selected_note_ids, tuple) or any(
            not isinstance(note_id, str) or not note_id for note_id in self.selected_note_ids
        ):
            raise ValueError("selected_note_ids must be a tuple of non-empty strings")
        if not isinstance(self.intent_template, str):
            raise ValueError("intent_template must be a string")
        if not isinstance(self.budget, MusicAgentTurnBudget):
            raise ValueError("budget must be MusicAgentTurnBudget")
        if self.append_after_seconds is not None and (
            isinstance(self.append_after_seconds, bool)
            or not isinstance(self.append_after_seconds, (int, float))
            or not math.isfinite(self.append_after_seconds)
            or self.append_after_seconds < 0
        ):
            raise ValueError("append_after_seconds must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class StagedMusicOperation:
    """A validated operation and its compact accounting data."""

    name: str
    payload: Mapping[str, object]
    added_notes: int = 0
    updated_notes: int = 0
    deleted_notes: int = 0
    summary: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "operation name")
        object.__setattr__(self, "payload", _frozen_mapping(self.payload, "operation payload"))
        _require_non_negative_int(self.added_notes, "added_notes")
        _require_non_negative_int(self.updated_notes, "updated_notes")
        _require_non_negative_int(self.deleted_notes, "deleted_notes")
        if not isinstance(self.summary, str):
            raise ValueError("summary must be a string")


@dataclass(frozen=True, slots=True)
class MusicToolResult:
    """Compact normalized tool outcome that is safe to return to a provider."""

    success: bool
    tool_name: str
    summary: str
    data: Mapping[str, object] = field(default_factory=dict)
    added_notes: int = 0
    updated_notes: int = 0
    deleted_notes: int = 0
    warnings: tuple[str, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        _require_text(self.tool_name, "tool_name")
        if not isinstance(self.summary, str):
            raise ValueError("summary must be a string")
        object.__setattr__(self, "data", _frozen_mapping(self.data, "result data"))
        for field_name in ("added_notes", "updated_notes", "deleted_notes"):
            _require_non_negative_int(getattr(self, field_name), field_name)
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(warning, str) for warning in self.warnings
        ):
            raise ValueError("warnings must be a tuple of strings")
        if self.success:
            if self.error_code is not None:
                raise ValueError("successful result cannot have an error code")
        elif self.error_code not in _ERROR_CODES:
            raise ValueError("failure result must use a stable error code")

    @classmethod
    def success_result(
        cls,
        tool_name: str,
        summary: str,
        *,
        data: Mapping[str, object] | None = None,
        added_notes: int = 0,
        updated_notes: int = 0,
        deleted_notes: int = 0,
        warnings: tuple[str, ...] = (),
    ) -> MusicToolResult:
        return cls(
            success=True,
            tool_name=tool_name,
            summary=summary,
            data=data or {},
            added_notes=added_notes,
            updated_notes=updated_notes,
            deleted_notes=deleted_notes,
            warnings=warnings,
        )

    @classmethod
    def failure(
        cls,
        tool_name: str,
        error_code: str,
        summary: str,
        *,
        data: Mapping[str, object] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> MusicToolResult:
        return cls(
            success=False,
            tool_name=tool_name,
            summary=summary,
            data=data or {},
            warnings=warnings,
            error_code=error_code,
        )


@dataclass(frozen=True, slots=True)
class MusicEditProposal:
    """In-memory write proposal for later Phase 4 review/application."""

    project_identity: str
    baseline_revision: int
    authorized_bars: tuple[BarRange, ...]
    operations: tuple[StagedMusicOperation, ...]
    change_summary: Mapping[str, int]
    status: MusicEditProposalStatus = MusicEditProposalStatus.PENDING
    proposal_id: str = ""
    intent_template: str = ""

    def __post_init__(self) -> None:
        _require_text(self.project_identity, "project_identity")
        _require_non_negative_int(self.baseline_revision, "baseline_revision")
        if not isinstance(self.authorized_bars, tuple) or any(
            not isinstance(item, BarRange) for item in self.authorized_bars
        ):
            raise ValueError("authorized_bars must be a tuple of BarRange")
        if not isinstance(self.operations, tuple) or any(
            not isinstance(item, StagedMusicOperation) for item in self.operations
        ):
            raise ValueError("operations must be a tuple of StagedMusicOperation")
        summary = _frozen_mapping(self.change_summary, "change_summary")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in summary.values()
        ):
            raise ValueError("change_summary values must be non-negative integers")
        if not isinstance(self.status, MusicEditProposalStatus):
            raise ValueError("status must be MusicEditProposalStatus")
        if self.proposal_id:
            _require_text(self.proposal_id, "proposal_id")
        elif not isinstance(self.proposal_id, str):
            raise ValueError("proposal_id must be a string")
        if not isinstance(self.intent_template, str):
            raise ValueError("intent_template must be a string")
        object.__setattr__(self, "change_summary", summary)
