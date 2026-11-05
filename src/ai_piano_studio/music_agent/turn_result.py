"""Structured, UI-safe evidence for one Assistant turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssistantTurnResult:
    status: str
    intent: str
    mode: str
    tool_call_count: int
    read_count: int
    write_count: int
    proposal_id: str
    proposal_status: str
    revision_before: int | None
    revision_after: int | None
    committed: bool
    capabilities: Mapping[str, object]
    final_text: str
    provider_calls: int = 0
    validation_model_repairs: int = 0
    local_repairs: int = 0

    def __post_init__(self) -> None:
        for field_name in ("status", "intent", "mode", "final_text"):
            if not isinstance(getattr(self, field_name), str) or not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in (
            "tool_call_count",
            "read_count",
            "write_count",
            "provider_calls",
            "validation_model_repairs",
            "local_repairs",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.read_count + self.write_count > self.tool_call_count:
            raise ValueError("read_count and write_count exceed tool_call_count")
        for field_name in ("revision_before", "revision_after"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer or None")
        if not isinstance(self.proposal_id, str) or not isinstance(self.proposal_status, str):
            raise ValueError("proposal fields must be strings")
        if bool(self.proposal_id) != bool(self.proposal_status):
            raise ValueError("proposal_id and proposal_status must be provided together")
        if not isinstance(self.committed, bool):
            raise ValueError("committed must be a boolean")
        if self.committed and (
            self.revision_before is None
            or self.revision_after is None
            or self.revision_after <= self.revision_before
        ):
            raise ValueError("revision_after must be greater than revision_before when committed")
        if not isinstance(self.capabilities, Mapping):
            raise ValueError("capabilities must be a mapping")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "intent": self.intent,
            "mode": self.mode,
            "toolCallCount": self.tool_call_count,
            "readCount": self.read_count,
            "writeCount": self.write_count,
            "proposalId": self.proposal_id,
            "proposalStatus": self.proposal_status,
            "revisionBefore": self.revision_before,
            "revisionAfter": self.revision_after,
            "committed": self.committed,
            "capabilities": dict(self.capabilities),
            "finalText": self.final_text,
            "providerCalls": self.provider_calls,
            "validationModelRepairs": self.validation_model_repairs,
            "localRepairs": self.local_repairs,
        }
