import json
import logging

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import DEFAULT_LAYER_ID, MusicLayer
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.persistence.project_json import (
    load_project,
    project_to_json,
    save_project,
)
from ai_piano_studio.persistence.project_document import (
    load_project_document,
    save_project_document,
)
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata


def seed_project() -> ProjectState:
    created_at = "2026-08-02T00:00:00.000+00:00"
    notes = (
        Note.create(
            id="a", pitch=60, start_time=0.0, duration=0.5, created_at=created_at,
        ),
        Note.create(
            id="b", pitch=64, start_time=1.0, duration=0.75, velocity=90, created_at=created_at,
        ),
    )
    return ProjectState(notes, frozenset({"a"}))


def test_project_json_contains_only_schema_tempo_and_notes() -> None:
    payload = json.loads(project_to_json(seed_project()))
    assert set(payload) == {
        "schemaVersion",
        "bpm",
        "timeSignature",
        "notes",
        "playbackNotes",
        "playbackTempo",
        "musicLayers", "controlEvents", "playbackControlEvents",
    }
    assert payload["schemaVersion"] == 5
    assert payload["bpm"] == 120
    assert payload["timeSignature"] == {"beatsPerBar": 4, "beatUnit": 4}
    assert payload["playbackTempo"] == {"bpm": 120, "beatsPerBar": 4, "beatUnit": 4}
    assert [note["id"] for note in payload["notes"]] == ["a", "b"]
    assert [note["id"] for note in payload["playbackNotes"]] == ["a", "b"]
    assert set(payload["notes"][0]) == {
        "id", "pitch", "noteName", "startTime",
        "duration", "velocity", "instrument", "status", "createdAt", "updatedAt",
        "releaseMode", "releaseMs",
        "layerId",
    }


def test_project_json_is_deterministic_utf8_text() -> None:
    first = project_to_json(seed_project())
    second = project_to_json(seed_project())
    assert first == second
    assert first.endswith("\n")


def test_save_project_writes_json_atomically(tmp_path) -> None:
    target = tmp_path / "demo.pianoai.json"
    save_project(target, seed_project())
    assert json.loads(target.read_text(encoding="utf-8"))["schemaVersion"] == 5
    assert not list(tmp_path.glob("*.tmp"))


def test_save_project_derives_playback_snapshot_from_authoritative_notes(tmp_path) -> None:
    target = tmp_path / "pending-save.pianoai.json"
    pending = Note.create(
        id="a", pitch=72, start_time=0.0, duration=0.5, status="pending"
    )
    stale_snapshot = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)

    save_project(target, ProjectState((pending,), playback_notes=(stale_snapshot,)))

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["notes"] == payload["playbackNotes"]
    assert payload["notes"][0]["pitch"] == 72
    assert payload["notes"][0]["status"] == "saved"


def test_save_project_rebuilds_stale_saved_playback_snapshot(tmp_path) -> None:
    target = tmp_path / "stale-saved-snapshot.pianoai.json"
    authoritative = Note.create(
        id="a", pitch=72, start_time=0.0, duration=0.5, status="saved"
    )
    stale_snapshot = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)

    save_project(target, ProjectState((authoritative,), playback_notes=(stale_snapshot,)))

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["notes"] == payload["playbackNotes"]
    assert payload["playbackNotes"][0]["pitch"] == 72


def test_load_project_roundtrips_notes_without_selection(tmp_path) -> None:
    target = tmp_path / "demo.pianoai.json"
    original = seed_project()
    save_project(target, original)

    loaded = load_project(target)

    assert loaded.notes == original.notes
    assert loaded.selected_ids == frozenset()
    assert loaded.tempo == TempoMap()


def test_custom_release_round_trips_without_changing_duration(tmp_path) -> None:
    note = Note.create(
        id="a",
        pitch=60,
        start_time=1.0,
        duration=0.5,
        release_mode="custom",
        release_ms=420.0,
    )
    target = tmp_path / "release.pianoai.json"

    save_project(target, ProjectState((note,)))
    loaded = load_project(target)

    assert loaded.notes[0].release_mode == "custom"
    assert loaded.notes[0].release_ms == pytest.approx(420.0)
    assert loaded.notes[0].duration == pytest.approx(0.5)


def test_old_note_payload_defaults_to_natural_release() -> None:
    payload = seed_project().notes[0].to_dict()
    payload.pop("releaseMode", None)
    payload.pop("releaseMs", None)

    restored = Note.from_dict(payload)

    assert restored.release_mode == "natural"
    assert restored.release_ms is None


def test_load_project_roundtrips_project_tempo(tmp_path) -> None:
    target = tmp_path / "waltz.pianoai.json"
    state = ProjectState(
        seed_project().notes,
        tempo=TempoMap(bpm=90, beats_per_bar=3, beat_unit=4),
    )
    save_project(target, state)

    loaded = load_project(target)

    assert loaded.tempo == TempoMap(bpm=90, beats_per_bar=3, beat_unit=4)


def test_load_project_accepts_v1_files_with_default_tempo(tmp_path) -> None:
    target = tmp_path / "legacy.json"
    legacy = {
        "schemaVersion": 1,
        "notes": [seed_project().notes[0].to_dict()],
    }
    target.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = load_project(target)

    assert [note.id for note in loaded.notes] == ["a"]
    assert loaded.tempo == TempoMap()
    assert loaded.layers[0].id == DEFAULT_LAYER_ID


def test_schema_v4_round_trips_ordered_music_layers() -> None:
    melody = MusicLayer.create(
        id="melody", name="主旋律", role="melody", pinned=True, order=0
    )
    default_layer = MusicLayer.default().with_changes(order=1)
    note = Note.create(
        id="a", pitch=60, start_time=0.0, duration=0.5, layer_id="melody"
    )
    state = ProjectState(
        (note,), layers=(melody, default_layer)
    )

    payload = json.loads(project_to_json(state))
    restored = load_project_from_payload(payload)

    assert payload["schemaVersion"] == 5
    assert [layer["id"] for layer in payload["musicLayers"]] == [
        "melody", DEFAULT_LAYER_ID
    ]
    assert payload["musicLayers"][0]["pinned"] is True
    assert restored == state.clear_selection()


def test_schema_v4_defaults_missing_pinned_metadata_to_false() -> None:
    payload = json.loads(project_to_json(seed_project()))
    for layer in payload["musicLayers"]:
        layer.pop("pinned", None)

    restored = load_project_from_payload(payload)

    assert all(layer.pinned is False for layer in restored.layers)


def test_v3_multitrack_migration_creates_stable_layers_and_membership() -> None:
    notes = (
        Note.create(id="track-0", pitch=60, start_time=0.0, duration=0.5, track=0, channel=0),
        Note.create(id="track-1", pitch=48, start_time=0.0, duration=0.5, track=1, channel=2),
    )
    payload = {
        "schemaVersion": 3,
        "bpm": 120,
        "timeSignature": {"beatsPerBar": 4, "beatUnit": 4},
        "notes": [note.to_dict() for note in notes],
        "playbackNotes": [note.to_dict() for note in notes],
        "playbackTempo": {"bpm": 120, "beatsPerBar": 4, "beatUnit": 4},
    }
    for item in payload["notes"] + payload["playbackNotes"]:
        item.pop("layerId", None)

    restored = load_project_from_payload(payload)

    assert len(restored.layers) == 2
    assert len({note.layer_id for note in restored.notes}) == 2
    assert {
        note.id: note.layer_id for note in restored.playback_notes
    } == {note.id: note.layer_id for note in restored.notes}


def load_project_from_payload(payload: dict) -> ProjectState:
    from ai_piano_studio.persistence.project_json import project_from_dict

    return project_from_dict(payload)


def test_load_project_ignores_legacy_scene_id(tmp_path) -> None:
    target = tmp_path / "legacy-scene.json"
    payload = json.loads(project_to_json(seed_project()))
    payload["sceneId"] = "sunset-haven"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(target)

    assert loaded.notes == seed_project().notes
    assert loaded.tempo == TempoMap()


def test_load_project_discards_orphan_playback_notes(tmp_path, caplog) -> None:
    target = tmp_path / "orphan-playback.json"
    editable = Note.create(id="editable", pitch=60, start_time=0.0, duration=0.5)
    orphan = Note.create(id="orphan", pitch=72, start_time=1.0, duration=0.5)
    target.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "bpm": 120,
                "timeSignature": {"beatsPerBar": 4, "beatUnit": 4},
                "notes": [editable.to_dict()],
                "playbackNotes": [orphan.to_dict()],
                "playbackTempo": {"bpm": 120, "beatsPerBar": 4, "beatUnit": 4},
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        loaded = load_project(target)

    assert loaded.notes == (editable,)
    assert loaded.playback_notes == (editable,)
    assert "orphan" in caplog.text


def test_load_project_rebuilds_stale_saved_playback_snapshot(tmp_path) -> None:
    target = tmp_path / "stale-saved-snapshot.json"
    authoritative = Note.create(
        id="a", pitch=72, start_time=0.0, duration=0.5, status="saved"
    )
    stale_snapshot = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    target.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "bpm": 120,
                "timeSignature": {"beatsPerBar": 4, "beatUnit": 4},
                "notes": [authoritative.to_dict()],
                "playbackNotes": [stale_snapshot.to_dict()],
                "playbackTempo": {"bpm": 120, "beatsPerBar": 4, "beatUnit": 4},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_project(target)

    assert loaded.playback_notes == loaded.notes == (authoritative,)
    assert loaded.playback_tempo == loaded.tempo


def test_load_project_rejects_wrong_schema_version(tmp_path) -> None:
    target = tmp_path / "future.json"
    target.write_text(json.dumps({"schemaVersion": 99, "notes": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="schemaVersion"):
        load_project(target)


def test_load_project_rejects_invalid_json(tmp_path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid project JSON"):
        load_project(target)


def test_load_project_rejects_missing_notes_list(tmp_path) -> None:
    target = tmp_path / "empty.json"
    target.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")

    with pytest.raises(ValueError, match="notes list"):
        load_project(target)


def test_project_json_disallows_nonstandard_float_tokens(monkeypatch) -> None:
    import ai_piano_studio.persistence.project_json as project_json

    monkeypatch.setattr(project_json, "project_to_dict", lambda state: {"value": float("nan")})

    with pytest.raises(ValueError):
        project_json.project_to_json(seed_project())


def test_document_save_load_roundtrips_optional_harmony_without_changing_state_api(tmp_path) -> None:
    from ai_piano_studio.persistence.project_document import ProjectDocument

    target = tmp_path / "harmony.pianoai.json"
    document = ProjectDocument(seed_project(), HarmonyProjectMetadata.empty())

    save_project_document(target, document)
    loaded = load_project_document(target)

    assert loaded.state.notes == document.state.notes
    assert loaded.state.selected_ids == frozenset()
    assert loaded.harmony == document.harmony
    assert "harmony" not in json.loads(target.read_text(encoding="utf-8"))


def test_document_save_logs_an_error_when_its_target_directory_is_missing(tmp_path, caplog) -> None:
    from ai_piano_studio.persistence.project_document import ProjectDocument

    target = tmp_path / "missing" / "harmony.pianoai.json"

    with caplog.at_level(logging.ERROR), pytest.raises(FileNotFoundError):
        save_project_document(target, ProjectDocument(seed_project()))

    assert "failed to save project document" in caplog.text.lower()


def test_document_save_logs_serialization_errors(tmp_path, caplog, monkeypatch) -> None:
    import ai_piano_studio.persistence.project_document as project_document
    from ai_piano_studio.persistence.project_document import ProjectDocument

    def fail_serialization(document) -> dict[str, object]:
        raise ValueError("invalid document")

    monkeypatch.setattr(project_document, "project_document_to_dict", fail_serialization)

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="invalid document"):
        save_project_document(tmp_path / "harmony.pianoai.json", ProjectDocument(seed_project()))

    assert "failed to save project document" in caplog.text.lower()
