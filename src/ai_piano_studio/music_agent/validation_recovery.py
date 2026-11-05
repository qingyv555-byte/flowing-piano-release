"""Bounded, mechanical recovery helpers for music-tool validation failures.

The normalizer is intentionally conservative.  It fixes representation errors
that have one unambiguous meaning and leaves musical decisions to the model
and the Gateway validator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite

from ai_piano_studio.music_agent.tool_contracts import MusicToolResult
from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry


TICK_EPSILON = 1e-5
BOUNDARY_EPSILON = 1e-6


class ToolErrorClass(StrEnum):
    """Recovery policy for one normalized tool failure."""

    LOCAL_REPAIRABLE = "local_repairable"
    MODEL_REPAIRABLE = "model_repairable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class ValidationFingerprint:
    """Stable, privacy-safe identity for one actionable validation item."""

    tool: str
    error_code: str
    item_index: int | None = None
    field: str = ""

    def __post_init__(self) -> None:
        if not self.tool or not self.error_code:
            raise ValueError("validation fingerprint requires tool and error_code")
        if self.item_index is not None and (
            not isinstance(self.item_index, int) or isinstance(self.item_index, bool)
            or self.item_index < 0
        ):
            raise ValueError("item_index must be a non-negative integer or None")
        if not isinstance(self.field, str):
            raise ValueError("field must be a string")


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """Normalized arguments plus an audit count of mechanical changes."""

    arguments: Mapping[str, object]
    changed: bool = False
    changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, Mapping):
            raise ValueError("normalized arguments must be a mapping")
        if not isinstance(self.changes, tuple) or any(
            not isinstance(item, str) for item in self.changes
        ):
            raise ValueError("changes must be a tuple of strings")
        if self.changed != bool(self.changes):
            raise ValueError("changed must match changes")


_TERMINAL_ERROR_CODES = frozenset(
    {
        "permission_denied",
        "limit_exceeded",
        "conflict",
        "cancelled",
        "unknown_tool",
        "execution_error",
        "security_violation",
    }
)
_LOCAL_REASONS = frozenset(
    {
        "integer_coercion",
        "tick_epsilon",
        "duplicate_item",
        "boundary_epsilon",
    }
)


def classify_tool_result(result: MusicToolResult) -> ToolErrorClass:
    """Classify a failure without turning permissions into model instructions."""

    if result.success:
        return ToolErrorClass.TERMINAL
    if result.error_code in _TERMINAL_ERROR_CODES:
        return ToolErrorClass.TERMINAL
    if result.error_code == "not_found":
        return ToolErrorClass.MODEL_REPAIRABLE
    if result.error_code == "validation_error":
        raw_items = result.data.get("invalid_items")
        if isinstance(raw_items, list) and raw_items:
            reasons = {
                item.get("reason")
                for item in raw_items
                if isinstance(item, Mapping)
            }
            if reasons and reasons.issubset(_LOCAL_REASONS):
                return ToolErrorClass.LOCAL_REPAIRABLE
        return ToolErrorClass.MODEL_REPAIRABLE
    return ToolErrorClass.TERMINAL


def validation_fingerprints(result: MusicToolResult) -> tuple[ValidationFingerprint, ...]:
    """Extract bounded fingerprints from a provider-safe validation result."""

    if result.success or result.error_code not in {"validation_error", "not_found"}:
        return ()
    raw_items = result.data.get("invalid_items")
    fingerprints: list[ValidationFingerprint] = []
    if isinstance(raw_items, list):
        for item in raw_items[:128]:
            if not isinstance(item, Mapping):
                continue
            item_index = item.get("item_index")
            if not isinstance(item_index, int) or isinstance(item_index, bool):
                item_index = None
            field = item.get("field", "")
            if not isinstance(field, str):
                field = ""
            fingerprints.append(
                ValidationFingerprint(
                    tool=result.tool_name,
                    error_code=result.error_code or "validation_error",
                    item_index=item_index,
                    field=field,
                )
            )
    if fingerprints:
        return tuple(fingerprints)
    return (
        ValidationFingerprint(
            tool=result.tool_name,
            error_code=result.error_code or "validation_error",
        ),
    )


def canonical_tick(value: object) -> object:
    """Snap a numeric tick only when it is within representation epsilon."""

    numeric: float
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return value
    else:
        return value
    if not isfinite(numeric):
        return value
    rounded = round(numeric)
    return int(rounded) if abs(numeric - rounded) <= TICK_EPSILON else value


class ToolArgumentNormalizer:
    """Apply only deterministic schema-aware argument repairs."""

    def __init__(self, registry: MusicToolRegistry) -> None:
        if not isinstance(registry, MusicToolRegistry):
            raise ValueError("registry must be MusicToolRegistry")
        self._registry = registry

    def normalize(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        authorized_bars: Sequence[object] = (),
        bar_duration_seconds: float | None = None,
    ) -> NormalizationResult:
        """Return a copied argument object; never mutate provider-owned input."""

        if not isinstance(arguments, Mapping):
            return NormalizationResult(arguments={})
        try:
            schema = self._registry.lookup(tool_name).input_schema
        except Exception:
            return NormalizationResult(arguments=deepcopy(dict(arguments)))
        changes: list[str] = []
        normalized = self._normalize_value(
            schema,
            deepcopy(dict(arguments)),
            "arguments",
            changes,
            deduplicate=self._is_write_tool(tool_name),
        )
        if isinstance(normalized, dict) and bar_duration_seconds is not None:
            self._snap_note_boundaries(
                tool_name,
                normalized,
                authorized_bars,
                bar_duration_seconds,
                changes,
            )
        if not isinstance(normalized, Mapping):
            normalized = deepcopy(dict(arguments))
        return NormalizationResult(
            arguments=normalized,
            changed=bool(changes),
            changes=tuple(changes),
        )

    def _normalize_value(
        self,
        schema: Mapping[str, object],
        value: object,
        path: str,
        changes: list[str],
        *,
        deduplicate: bool,
    ) -> object:
        expected_type = schema.get("type")
        if expected_type == "object" and isinstance(value, dict):
            properties = schema.get("properties")
            if isinstance(properties, Mapping):
                for key, property_schema in properties.items():
                    if key in value and isinstance(property_schema, Mapping):
                        value[key] = self._normalize_value(
                            property_schema,
                            value[key],
                            f"{path}.{key}",
                            changes,
                            deduplicate=deduplicate,
                        )
            return value
        if expected_type == "array" and isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(list(value)):
                    value[index] = self._normalize_value(
                        item_schema,
                        item,
                        f"{path}[{index}]",
                        changes,
                        deduplicate=deduplicate,
                    )
            if deduplicate:
                unique: list[object] = []
                for item in value:
                    if any(item == existing for existing in unique):
                        changes.append(f"{path}:duplicate_item")
                    else:
                        unique.append(item)
                value[:] = unique
            return value
        if expected_type == "integer":
            converted = self._integer_like(value)
            if converted is not value and (
                converted != value or type(converted) is not type(value)
            ):
                changes.append(f"{path}:integer_coercion")
                return converted
            return value
        if expected_type == "number":
            # Current contracts use seconds for musical positions.  Only
            # fields explicitly named as ticks may be epsilon-snapped here.
            leaf = path.rsplit(".", 1)[-1].lower()
            if "tick" in leaf:
                converted = canonical_tick(value)
                if converted != value:
                    changes.append(f"{path}:tick_epsilon")
                    return converted
        return value

    @staticmethod
    def _integer_like(value: object) -> int | object:
        if isinstance(value, bool) or isinstance(value, int):
            return value
        if isinstance(value, float):
            if isfinite(value) and value.is_integer():
                return int(value)
            return value
        if isinstance(value, str):
            try:
                decimal = Decimal(value.strip())
            except (InvalidOperation, ValueError):
                return value
            if decimal.is_finite() and decimal == decimal.to_integral_value():
                return int(decimal)
        return value

    def _snap_note_boundaries(
        self,
        tool_name: str,
        arguments: dict[str, object],
        authorized_bars: Sequence[object],
        bar_duration_seconds: float,
        changes: list[str],
    ) -> None:
        if tool_name not in {"add_notes", "replace_section"}:
            return
        if not isfinite(bar_duration_seconds) or bar_duration_seconds <= 0:
            return
        raw_notes = arguments.get("notes")
        if not isinstance(raw_notes, list):
            return
        spans: list[tuple[float, float]] = []
        for bar_range in authorized_bars:
            start_bar = getattr(bar_range, "start_bar", None)
            end_bar = getattr(bar_range, "end_bar", None)
            if isinstance(start_bar, int) and isinstance(end_bar, int):
                spans.append(
                    ((start_bar - 1) * bar_duration_seconds, end_bar * bar_duration_seconds)
                )
        if not spans:
            return
        for index, raw_note in enumerate(raw_notes):
            if not isinstance(raw_note, dict):
                continue
            start = raw_note.get("start_time")
            duration = raw_note.get("duration")
            if not isinstance(start, (int, float)) or isinstance(start, bool):
                continue
            if not isinstance(duration, (int, float)) or isinstance(duration, bool):
                continue
            end = float(start) + float(duration)
            for allowed_start, allowed_end in spans:
                snapped_start = float(start)
                snapped_duration = float(duration)
                if abs(snapped_start - allowed_start) <= BOUNDARY_EPSILON:
                    snapped_start = allowed_start
                if abs(end - allowed_end) <= BOUNDARY_EPSILON and allowed_end >= snapped_start:
                    snapped_duration = allowed_end - snapped_start
                if snapped_start != start:
                    raw_note["start_time"] = snapped_start
                    changes.append(f"arguments.notes[{index}].start_time:boundary_epsilon")
                if snapped_duration != duration:
                    raw_note["duration"] = snapped_duration
                    changes.append(f"arguments.notes[{index}].duration:boundary_epsilon")
                if snapped_start != start or snapped_duration != duration:
                    break

    def _is_write_tool(self, tool_name: str) -> bool:
        try:
            return not self._registry.lookup(tool_name).read_only
        except Exception:
            return False
