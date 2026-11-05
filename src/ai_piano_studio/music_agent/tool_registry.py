"""The single allowlisted registry for provider-visible music tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from ai_piano_studio.music_agent.tool_contracts import MusicPermission


class UnknownMusicToolError(LookupError):
    """Raised when a provider requests a tool outside the fixed allowlist."""


def _strict_object(
    properties: Mapping[str, object], required: Iterable[str] = (), **extra: object
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(required),
        "properties": dict(properties),
    }
    result.update(extra)
    return result


def _note_payload_schema() -> dict[str, object]:
    return _strict_object(
        {
            "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
            "start_time": {"type": "number", "minimum": 0},
            "duration": {"type": "number", "exclusiveMinimum": 0},
            "velocity": {"type": "integer", "minimum": 0, "maximum": 127},
            "instrument": {"type": "string", "minLength": 1, "maxLength": 64},
            "layer_id": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        ("pitch", "start_time", "duration", "velocity"),
    )


def _note_update_schema() -> dict[str, object]:
    return _strict_object(
        {
            "id": {"type": "string", "minLength": 1, "maxLength": 128},
            "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
            "start_time": {"type": "number", "minimum": 0},
            "duration": {"type": "number", "exclusiveMinimum": 0},
            "velocity": {"type": "integer", "minimum": 0, "maximum": 127},
            "instrument": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        ("id",),
        minProperties=2,
    )


def _bar_range_properties() -> dict[str, object]:
    return {
        "start_bar": {"type": "integer", "minimum": 1},
        "end_bar": {"type": "integer", "minimum": 1},
    }


@dataclass(frozen=True, slots=True)
class MusicToolDefinition:
    """Metadata/schema only; executable handlers are intentionally absent."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    permission: MusicPermission
    read_only: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("tool name is required")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("tool description is required")
        if not isinstance(self.input_schema, Mapping):
            raise ValueError("tool input_schema must be a mapping")
        if not isinstance(self.permission, MusicPermission):
            raise ValueError("tool permission must be MusicPermission")
        if not isinstance(self.read_only, bool):
            raise ValueError("tool read_only must be a boolean")
        object.__setattr__(self, "input_schema", MappingProxyType(deepcopy(dict(self.input_schema))))

    def openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(dict(self.input_schema)),
                "strict": True,
            },
        }


class MusicToolRegistry:
    """Ordered fixed registry with no name-to-Python-method bridge."""

    def __init__(self, definitions: Iterable[MusicToolDefinition]) -> None:
        ordered = tuple(definitions)
        if not ordered or any(not isinstance(item, MusicToolDefinition) for item in ordered):
            raise ValueError("definitions must be non-empty MusicToolDefinition values")
        names = tuple(item.name for item in ordered)
        if len(names) != len(set(names)):
            raise ValueError("duplicate music tool name")
        self._definitions = MappingProxyType({item.name: item for item in ordered})
        self._names = names

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @classmethod
    def default(cls) -> MusicToolRegistry:
        bar_range = _bar_range_properties()
        note_payload = _note_payload_schema()
        note_update = _note_update_schema()
        note_ids = {
            "type": "array",
            "minItems": 1,
            "maxItems": 2_000,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        }
        definitions = (
            MusicToolDefinition(
                "get_project_summary",
                "Read a compact summary of the authorized music project.",
                _strict_object({}),
                MusicPermission.READ_PROJECT_SUMMARY,
                True,
            ),
            MusicToolDefinition(
                "get_notes",
                "Read notes overlapping an authorized bar range, including crossing notes. Use next_offset to read further notes, "
                "especially when feedback was truncated. Write authorization is unchanged.",
                _strict_object({**bar_range, 'offset': dict(type='integer', minimum=0),
                                'limit': dict(type='integer', minimum=1, maximum=2000)}, ("start_bar", "end_bar")),
                MusicPermission.READ_NOTES,
                True,
            ),
            MusicToolDefinition(
                "get_harmony_context",
                "Read current Harmony candidates inside an authorized bar range.",
                _strict_object(bar_range, ("start_bar", "end_bar")),
                MusicPermission.READ_HARMONY,
                True,
            ),
            MusicToolDefinition(
                "add_notes",
                "Add validated notes inside authorized bars.",
                _strict_object(
                    {"notes": {"type": "array", "minItems": 1, "maxItems": 2_000, "items": note_payload}},
                    ("notes",),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "update_notes",
                "Update existing authorized notes without changing their identities.",
                _strict_object(
                    {"updates": {"type": "array", "minItems": 1, "maxItems": 2_000, "items": note_update}},
                    ("updates",),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "delete_notes",
                "Delete existing authorized notes by id.",
                _strict_object({"note_ids": note_ids}, ("note_ids",)),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "transpose_notes",
                "Transpose existing authorized notes by a bounded semitone count.",
                _strict_object(
                    {
                        "note_ids": note_ids,
                        "semitones": {"type": "integer", "minimum": -127, "maximum": 127},
                    },
                    ("note_ids", "semitones"),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "set_note_velocity",
                "Set velocity for existing authorized notes.",
                _strict_object(
                    {
                        "note_ids": note_ids,
                        "velocity": {"type": "integer", "minimum": 0, "maximum": 127},
                    },
                    ("note_ids", "velocity"),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "set_bpm",
                "Set the project BPM through the explicit BPM permission.",
                _strict_object({"bpm": {"type": "integer", "minimum": 60, "maximum": 300}}, ("bpm",)),
                MusicPermission.WRITE_BPM,
                False,
            ),
            MusicToolDefinition(
                "apply_chord",
                "Apply one current Harmony candidate selected by measure, index, and token.",
                _strict_object(
                    {
                        "measure_index": {"type": "integer", "minimum": 0},
                        "candidate_index": {"type": "integer", "minimum": 0},
                        "harmony_context_token": {"type": "string", "minLength": 1, "maxLength": 128},
                    },
                    ("measure_index", "candidate_index", "harmony_context_token"),
                ),
                MusicPermission.WRITE_HARMONY,
                False,
            ),
            MusicToolDefinition(
                "repeat_section",
                "Copy notes from an authorized source range to an authorized target start bar. "
                "Only for an explicitly requested repeat, loop, duplicate or reprise; "
                "do not use to satisfy continuation/extension by replaying existing phrases.",
                _strict_object(
                    {
                        "source_start_bar": {"type": "integer", "minimum": 1},
                        "source_end_bar": {"type": "integer", "minimum": 1},
                        "target_start_bar": {"type": "integer", "minimum": 1},
                    },
                    ("source_start_bar", "source_end_bar", "target_start_bar"),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
            MusicToolDefinition(
                "replace_section",
                "Atomically replace notes in one authorized target range.",
                _strict_object(
                    {
                        **bar_range,
                        "notes": {"type": "array", "minItems": 0, "maxItems": 2_000, "items": note_payload},
                    },
                    ("start_bar", "end_bar", "notes"),
                ),
                MusicPermission.WRITE_NOTES,
                False,
            ),
        )
        return cls(definitions)

    def lookup(self, name: object) -> MusicToolDefinition:
        if not isinstance(name, str):
            raise UnknownMusicToolError("tool is not registered")
        try:
            return self._definitions[name]
        except KeyError as error:
            raise UnknownMusicToolError("tool is not registered") from error

    def openai_schemas(self, *, read_only: bool = False) -> list[dict[str, object]]:
        """Return provider schemas, optionally restricted to the read-only registry subset."""
        return [
            self._definitions[name].openai_schema()
            for name in self._names
            if not read_only or self._definitions[name].read_only
        ]
