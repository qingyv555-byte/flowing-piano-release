import json

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.persistence.autosave import (
    META_NAME,
    SNAPSHOT_NAME,
    NullRecoveryStore,
    RecoveryStore,
    default_recovery_store,
)
from ai_piano_studio.persistence.project_document import ProjectDocument
from ai_piano_studio.harmony.project_metadata import HarmonyProjectMetadata


def project_with_note() -> ProjectState:
    note = Note.create(id="n1", pitch=64, start_time=0.5, duration=0.75)
    return ProjectState((note,))


def test_write_then_read_roundtrips_state_and_path(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    state = project_with_note()
    store.write(state, "C:/projects/song.pianoai.json")

    assert store.available is True
    result = store.read()
    assert result is not None
    restored, original_path = result
    assert restored.notes == state.notes
    assert original_path == "C:/projects/song.pianoai.json"
    meta = json.loads((tmp_path / META_NAME).read_text(encoding="utf-8"))
    assert meta["originalPath"] == "C:/projects/song.pianoai.json"
    assert "savedAt" in meta


def test_document_adapter_roundtrips_harmony_while_legacy_adapter_stays_state_only(
    tmp_path,
) -> None:
    store = RecoveryStore(tmp_path)
    state = project_with_note()
    metadata = HarmonyProjectMetadata.from_dict(
        {"schemaVersion": 1, "futureRoot": {"kept": True}}, state
    )

    store.write_document(ProjectDocument(state, metadata), "song.pianoai.json")

    document_result = store.read_document()
    assert document_result is not None
    assert document_result[0].harmony == metadata
    legacy_result = store.read()
    assert legacy_result is not None
    assert legacy_result == (state, "song.pianoai.json")


def test_read_without_snapshot_returns_none(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    assert store.available is False
    assert store.read() is None


def test_write_without_original_path_reads_back_none(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    store.write(project_with_note(), None)
    result = store.read()
    assert result is not None
    assert result[1] is None


def test_corrupt_snapshot_returns_none(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    (tmp_path / SNAPSHOT_NAME).write_text("{ not json", encoding="utf-8")
    assert store.read() is None


def test_corrupt_meta_still_restores_project_without_path(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    state = project_with_note()
    store.write(state, "C:/projects/song.pianoai.json")
    (tmp_path / META_NAME).write_text("{ broken", encoding="utf-8")

    result = store.read()
    assert result is not None
    restored, original_path = result
    assert restored.notes == state.notes
    assert original_path is None


def test_clear_removes_snapshot_and_is_idempotent(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    store.write(project_with_note(), None)
    store.clear()
    assert store.available is False
    store.clear()
    assert store.read() is None


def test_info_returns_metadata_for_recovery_card(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    store.write(project_with_note(), "C:/projects/song.pianoai.json")

    info = store.info()
    assert info is not None
    assert info["name"] == "song"
    assert info["originalPath"] == "C:/projects/song.pianoai.json"
    assert info["noteCount"] == 1
    assert info["durationSeconds"] == 1.25
    assert info["savedAt"]


def test_info_names_unnamed_snapshot(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    store.write(project_with_note(), None)
    info = store.info()
    assert info is not None
    assert info["name"] == "未命名工程"


def test_info_returns_none_without_snapshot(tmp_path) -> None:
    assert RecoveryStore(tmp_path).info() is None


def test_info_survives_corrupt_meta(tmp_path) -> None:
    store = RecoveryStore(tmp_path)
    store.write(project_with_note(), "C:/projects/song.pianoai.json")
    (tmp_path / META_NAME).write_text("{ broken", encoding="utf-8")
    info = store.info()
    assert info is not None
    assert info["name"] == "未命名工程"
    assert info["noteCount"] is None


def test_null_store_is_a_no_op() -> None:
    store = NullRecoveryStore()
    store.write(project_with_note(), "x.pianoai.json")
    assert store.available is False
    assert store.read() is None
    assert store.info() is None
    store.write_document(ProjectDocument(project_with_note()), "x.pianoai.json")
    assert store.read_document() is None
    store.clear()


def test_default_store_is_null_without_application_names() -> None:
    assert isinstance(default_recovery_store(), NullRecoveryStore)
