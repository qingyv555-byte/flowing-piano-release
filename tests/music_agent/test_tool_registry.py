from __future__ import annotations

import pytest

from ai_piano_studio.music_agent.tool_contracts import MusicPermission
from ai_piano_studio.music_agent.tool_registry import (
    MusicToolRegistry,
    UnknownMusicToolError,
)


EXPECTED_TOOL_NAMES = (
    "get_project_summary",
    "get_notes",
    "get_harmony_context",
    "add_notes",
    "update_notes",
    "delete_notes",
    "transpose_notes",
    "set_note_velocity",
    "set_bpm",
    "apply_chord",
    "repeat_section",
    "replace_section",
)


def test_default_registry_contains_exactly_the_approved_twelve_tools() -> None:
    """Break caught: a provider is offered an omitted, duplicate, or unapproved music tool."""
    registry = MusicToolRegistry.default()

    assert registry.names == EXPECTED_TOOL_NAMES


def test_set_bpm_schema_requires_only_the_bpm_write_permission() -> None:
    """Break caught: BPM edits accidentally inherit broad note/project write access."""
    definition = MusicToolRegistry.default().lookup("set_bpm")

    assert definition.permission is MusicPermission.WRITE_BPM
    assert definition.read_only is False
    assert definition.input_schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["bpm"],
        "properties": {
            "bpm": {"type": "integer", "minimum": 60, "maximum": 300},
        },
    }


def test_provider_schemas_are_derived_from_the_single_registry_definitions() -> None:
    """Break caught: provider schemas diverge from the registry allowlist or strict payloads."""
    registry = MusicToolRegistry.default()
    schemas = registry.openai_schemas()

    assert [schema["function"]["name"] for schema in schemas] == list(EXPECTED_TOOL_NAMES)
    assert all(schema["type"] == "function" for schema in schemas)
    assert all(schema["function"]["strict"] is True for schema in schemas)
    assert all(
        schema["function"]["parameters"]["additionalProperties"] is False
        for schema in schemas
    )
    assert schemas[8]["function"]["parameters"] == registry.lookup("set_bpm").input_schema


def test_read_only_provider_schemas_exclude_every_write_tool() -> None:
    """Break caught: a music-analysis turn receives tools that can edit a project."""
    registry = MusicToolRegistry.default()

    schemas = registry.openai_schemas(read_only=True)

    assert [schema["function"]["name"] for schema in schemas] == [
        "get_project_summary",
        "get_notes",
        "get_harmony_context",
    ]


@pytest.mark.parametrize(
    "tool_name",
    [
        "read_file",
        "write_file",
        "shell",
        "PowerShell",
        "python",
        "eval",
        "exec",
        "__import__",
        "Git",
        "mouse",
        "keyboard",
        "delete_project",
        "qml_direct_control",
        "filesystem",
        "get_notes ",
        "GET_NOTES",
    ],
)
def test_registry_rejects_unknown_and_privileged_tool_names_at_lookup(tool_name: str) -> None:
    """Break caught: an unregistered name can fall through to executable code."""
    with pytest.raises(UnknownMusicToolError):
        MusicToolRegistry.default().lookup(tool_name)


def test_registry_rejects_non_string_lookup_names() -> None:
    """Break caught: non-string provider payloads reach a dynamic dispatch path."""
    with pytest.raises(UnknownMusicToolError):
        MusicToolRegistry.default().lookup({"name": "get_notes"})


def test_package_exports_gateway_and_fixed_registry_api() -> None:
    """Break caught: providers cannot consume the approved tool API from the package boundary."""
    from ai_piano_studio.music_agent import (
        MusicAgentToolGateway as PublicGateway,
        MusicToolRegistry as PublicRegistry,
    )

    assert PublicGateway.__name__ == "MusicAgentToolGateway"
    assert PublicRegistry.default().names == MusicToolRegistry.default().names
