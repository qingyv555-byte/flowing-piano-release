from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)
from ai_piano_studio.persistence.autosave import RecoveryStore
from ai_piano_studio.persistence.project_document import (
    ProjectDocument,
    load_project_document,
    save_project_document,
)
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary


def harmony_document() -> ProjectDocument:
    notes = tuple(
        Note.create(
            id=note_id,
            pitch=pitch,
            start_time=0.0,
            duration=2.0,
            velocity=68,
        )
        for note_id, pitch in (("stable-c", 48), ("stable-e", 52), ("stable-g", 55))
    )
    state = ProjectState(notes)
    group = HarmonyGroup(
        group_id="stable-group-id",
        measure_index=0,
        start_tick=0,
        duration_ticks=1920,
        chord_symbol="C",
        voicing_pitches=(48, 52, 55),
        generated_note_ids=tuple(note.id for note in notes),
        original_signatures={note.id: NoteSignature.from_note(note) for note in notes},
        detached=False,
        application_origin="auto",
        replace_policy="automatic",
        _unknown_json='{"futureGroup":{"kept":true}}',
    )
    metadata = HarmonyProjectMetadata(
        (group,),
        _unknown_json='{"futureRoot":{"version":2}}',
    )
    return ProjectDocument(state, metadata)


def test_manual_save_and_reopen_roundtrip_harmony_and_metadata_dirty(
    qtbot, tmp_path
) -> None:
    document = harmony_document()
    controller = EditorController(document.state)
    assert controller.isDirty is False

    assert controller.apply_harmony_transaction(document.state, document.harmony)
    assert controller.isDirty is True

    target = tmp_path / "roundtrip.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2000):
        controller.saveProject(str(target))
    assert controller.isDirty is False

    stored = load_project_document(target)
    assert stored.harmony.groups[0].group_id == "stable-group-id"
    assert stored.harmony.to_dict()["futureRoot"] == {"version": 2}
    assert stored.harmony.groups[0].to_dict()["futureGroup"] == {"kept": True}

    reopened = EditorController()
    with qtbot.waitSignal(reopened.loadSucceeded, timeout=500):
        reopened.loadProject(str(target))
    assert reopened.harmony_metadata == stored.harmony
    assert reopened.isDirty is False

    changed_metadata = replace(
        reopened.harmony_metadata,
        _unknown_json='{"futureRoot":{"version":3}}',
    )
    assert reopened.apply_harmony_transaction(reopened.state, changed_metadata)
    assert reopened.isDirty is True


def test_whole_project_origin_roundtrips_without_losing_group_ownership(tmp_path) -> None:
    document = harmony_document()
    whole_project_group = replace(
        document.harmony.groups[0],
        application_origin="whole-project",
        replace_policy="explicit-only",
    )
    target = tmp_path / "whole-project.pianoai.json"

    save_project_document(
        target,
        ProjectDocument(
            document.state,
            HarmonyProjectMetadata((whole_project_group,), document.harmony._unknown_json),
        ),
    )
    restored = load_project_document(target)

    assert len(restored.harmony.groups) == 1
    restored_group = restored.harmony.groups[0]
    assert restored_group.group_id == "stable-group-id"
    assert restored_group.generated_note_ids == ("stable-c", "stable-e", "stable-g")
    assert restored_group.original_signatures == whole_project_group.original_signatures
    assert restored_group.application_origin == "whole-project"
    assert restored_group.replace_policy == "explicit-only"


def test_recovery_restores_full_document_as_dirty(tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    document = harmony_document()
    store.write_document(document, "C:/projects/original.pianoai.json")
    controller = EditorController(recovery_store=store)

    assert controller.restoreRecovery() is True

    assert controller.harmony_metadata == document.harmony
    assert controller.currentFilePath == "C:/projects/original.pianoai.json"
    assert controller.isDirty is True


def test_recovery_timer_writes_current_harmony_document(tmp_path) -> None:
    store = RecoveryStore(tmp_path / "recovery")
    document = harmony_document()
    controller = EditorController(document.state, recovery_store=store)
    controller.apply_harmony_transaction(document.state, document.harmony)

    controller._write_recovery_now()

    recovered = store.read_document()
    assert recovered is not None
    restored, _ = recovered
    assert restored.harmony == document.harmony


def test_copy_preserves_project_bytes_and_rename_does_not_edit_json(tmp_path) -> None:
    library = ProjectLibrary(tmp_path / "Projects")
    source = library.next_project_path()
    source.parent.mkdir(parents=True)
    save_project_document(source, harmony_document())
    original = source.read_bytes()
    controller = EditorController(project_library=library)

    controller.copyProject(str(source))
    copied = tmp_path / "Projects" / "project-002" / "project.pianoai.json"
    assert copied.read_bytes() == original
    assert load_project_document(copied).harmony.groups[0].group_id == "stable-group-id"

    controller.renameProject(str(source), "和弦练习")
    assert source.read_bytes() == original
    assert library.display_name_for(source) == "和弦练习"


def test_legacy_project_opens_without_harmony_or_writeback(qtbot, tmp_path) -> None:
    target = tmp_path / "legacy.pianoai.json"
    save_project(
        target,
        ProjectState(
            (Note.create(id="legacy", pitch=64, start_time=0.0, duration=0.5),)
        ),
    )
    original = target.read_bytes()
    controller = EditorController()

    with qtbot.waitSignal(controller.loadSucceeded, timeout=500):
        controller.loadProject(str(target))

    assert controller.harmony_metadata == HarmonyProjectMetadata.empty()
    assert controller.isDirty is False
    assert target.read_bytes() == original
