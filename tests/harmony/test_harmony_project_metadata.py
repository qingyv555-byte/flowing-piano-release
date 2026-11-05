from __future__ import annotations

from dataclasses import FrozenInstanceError
from copy import deepcopy
import logging

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata
from ai_piano_studio.persistence.project_document import (
    ProjectDocument,
    project_document_from_dict,
    project_document_to_dict,
)
from ai_piano_studio.persistence.project_json import project_to_dict


_CREATED_AT = "2026-08-02T00:00:00.000+00:00"


def _state() -> ProjectState:
    return ProjectState(
        (
            Note.create(
                id="harmony-a",
                pitch=48,
                start_time=0.0,
                duration=1.0,
                velocity=72,
                instrument="piano",
                created_at=_CREATED_AT,
            ),
            Note.create(
                id="harmony-b",
                pitch=52,
                start_time=0.0,
                duration=1.0,
                velocity=72,
                instrument="piano",
                created_at=_CREATED_AT,
            ),
        )
    )


def _signature(note: Note) -> dict[str, object]:
    return {
        "pitch": note.pitch,
        "startTime": note.start_time,
        "duration": note.duration,
        "velocity": note.velocity,
        "instrument": note.instrument,
    }


def _group(group_id: str, note_ids: list[str]) -> dict[str, object]:
    notes_by_id = {note.id: note for note in _state().notes}
    return {
        "groupId": group_id,
        "generatedBy": "harmony-assist",
        "measureIndex": 0,
        "startTick": 0,
        "durationTicks": 1920,
        "chordSymbol": "C",
        "voicingPitches": [48, 52, 55],
        "generatedNoteIds": note_ids,
        "originalSignatures": {
            note_id: _signature(notes_by_id[note_id]) for note_id in note_ids
        },
        "detached": False,
        "applicationOrigin": "auto",
        "replacePolicy": "automatic",
    }


def _payload_with_group() -> dict[str, object]:
    payload = project_to_dict(_state())
    payload["harmony"] = {
        "schemaVersion": 1,
        "groups": [_group("group-a", ["harmony-a", "harmony-b"])],
    }
    return payload


def test_old_project_versions_produce_empty_metadata_without_mutating_payload() -> None:
    current = project_to_dict(_state())
    legacy_payloads = (
        {"schemaVersion": 1, "notes": current["notes"]},
        {"schemaVersion": 2, "bpm": 120, "notes": current["notes"]},
        current,
    )

    for payload in legacy_payloads:
        original = deepcopy(payload)

        document = project_document_from_dict(payload)

        assert document.harmony == HarmonyProjectMetadata.empty()
        assert payload == original


def test_document_roundtrip_preserves_valid_groups_and_nested_unknown_values() -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    harmony["futureRoot"] = {"nested": [None, {"answer": 42}]}
    group = harmony["groups"][0]
    assert isinstance(group, dict)
    group["futureGroup"] = ["retained", {"deep": True}]

    document = project_document_from_dict(payload)
    restored = project_document_to_dict(document)

    assert document.harmony.groups[0].group_id == "group-a"
    assert document.harmony.groups[0].generated_note_ids == ("harmony-a", "harmony-b")
    assert restored["harmony"] == harmony


def test_non_empty_metadata_serialization_writes_internal_schema_version() -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    harmony["schemaVersion"] = 1
    parsed = project_document_from_dict(payload)
    metadata_without_raw_root_fields = HarmonyProjectMetadata(parsed.harmony.groups)

    restored = project_document_to_dict(ProjectDocument(parsed.state, metadata_without_raw_root_fields))

    assert restored["harmony"]["schemaVersion"] == 1


@pytest.mark.parametrize("internal_version", [2, "1", None])
def test_unsupported_or_malformed_internal_version_is_isolated_with_warning(
    internal_version,
    caplog,
) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    harmony["schemaVersion"] = internal_version

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.state.notes == _state().notes
    assert document.harmony == HarmonyProjectMetadata.empty()
    assert "schemaversion" in caplog.text.lower()


def test_missing_internal_version_is_isolated_with_warning(caplog) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    del harmony["schemaVersion"]

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.state.notes == _state().notes
    assert document.harmony == HarmonyProjectMetadata.empty()
    assert "schemaversion" in caplog.text.lower()


def test_unrepresentable_signature_number_drops_only_corrupt_group_with_warning(caplog) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    corrupt = _group("corrupt", ["harmony-a"])
    corrupt["originalSignatures"]["harmony-a"]["startTime"] = 10**400
    harmony["groups"] = [corrupt, _group("valid", ["harmony-b"])]

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.state.notes == _state().notes
    assert [group.group_id for group in document.harmony.groups] == ["valid"]
    assert "corrupt" in caplog.text.lower()


def test_signature_mismatch_and_missing_signature_detach_without_losing_group(
    caplog,
) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    group = harmony["groups"][0]
    assert isinstance(group, dict)
    signatures = group["originalSignatures"]
    assert isinstance(signatures, dict)
    signatures["harmony-a"]["pitch"] = 47
    del signatures["harmony-b"]

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    retained = document.harmony.groups[0]
    assert retained.generated_note_ids == ("harmony-a", "harmony-b")
    assert retained.detached is True
    assert "signature" in caplog.text.lower()


def test_corrupt_groups_are_isolated_and_duplicate_note_owner_keeps_first(
    caplog,
) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    first = harmony["groups"][0]
    assert isinstance(first, dict)
    second = _group("second", ["harmony-a"])
    corrupt = _group("corrupt", ["harmony-b"])
    del corrupt["groupId"]
    harmony["groups"] = [first, second, corrupt]

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.state.notes
    assert [group.group_id for group in document.harmony.groups] == ["group-a"]
    assert "duplicate" in caplog.text.lower()
    assert "skipping" in caplog.text.lower()


def test_non_object_harmony_is_ignored_with_a_warning_without_blocking_project_load(caplog) -> None:
    payload = project_to_dict(_state())
    payload["harmony"] = ["corrupt"]

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.state.notes == _state().notes
    assert document.harmony == HarmonyProjectMetadata.empty()
    assert "harmony" in caplog.text.lower()


def test_explicit_null_harmony_is_ignored_with_a_warning(caplog) -> None:
    payload = project_to_dict(_state())
    payload["harmony"] = None

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.harmony == HarmonyProjectMetadata.empty()
    assert "malformed harmony" in caplog.text.lower()


def test_empty_document_metadata_omits_harmony() -> None:
    payload = project_document_to_dict(ProjectDocument(_state(), HarmonyProjectMetadata.empty()))

    assert "harmony" not in payload


def test_unknown_root_values_keep_harmony_present_when_groups_are_empty() -> None:
    payload = project_to_dict(_state())
    payload["harmony"] = {
        "schemaVersion": 1,
        "futureRoot": {"nested": [None, {"safe": True}]},
    }

    restored = project_document_to_dict(project_document_from_dict(payload))

    assert restored["harmony"] == {
        "schemaVersion": 1,
        "futureRoot": {"nested": [None, {"safe": True}]},
        "groups": [],
    }


def test_missing_note_ids_are_removed_without_discarding_valid_group(caplog) -> None:
    payload = _payload_with_group()
    harmony = payload["harmony"]
    assert isinstance(harmony, dict)
    group = harmony["groups"][0]
    assert isinstance(group, dict)
    group["generatedNoteIds"].append("missing")
    group["originalSignatures"]["missing"] = _signature(_state().notes[0])

    with caplog.at_level(logging.WARNING):
        document = project_document_from_dict(payload)

    assert document.harmony.groups[0].generated_note_ids == ("harmony-a", "harmony-b")
    assert "missing harmony note" in caplog.text.lower()


def test_metadata_values_are_frozen_and_keep_signature_mapping_read_only() -> None:
    metadata = project_document_from_dict(_payload_with_group()).harmony
    group = metadata.groups[0]

    with pytest.raises(FrozenInstanceError):
        group.detached = True
    with pytest.raises(TypeError):
        group.original_signatures["harmony-a"] = group.original_signatures["harmony-a"]
