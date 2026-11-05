from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.harmony.controller import HarmonySuggestionController
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)
from ai_piano_studio.harmony.settings import HarmonySettings
from ai_piano_studio.persistence.project_library import ProjectLibrary


def _transaction() -> tuple[ProjectState, HarmonyProjectMetadata]:
    notes = tuple(
        Note.create(
            id=note_id,
            pitch=pitch,
            start_time=start,
            duration=1.0,
            velocity=72,
        )
        for note_id, pitch, start in (
            ("generated-c", 48, 0.0),
            ("generated-e", 52, 0.0),
            ("generated-g", 55, 0.0),
        )
    )
    group = HarmonyGroup(
        group_id="group-c-major",
        measure_index=0,
        start_tick=0,
        duration_ticks=1920,
        chord_symbol="C",
        voicing_pitches=(48, 52, 55),
        generated_note_ids=tuple(note.id for note in notes),
        original_signatures={
            note.id: NoteSignature.from_note(note) for note in notes
        },
        detached=False,
        application_origin="auto",
        replace_policy="automatic",
    )
    return ProjectState(notes), HarmonyProjectMetadata((group,))


def _managed_and_detached_metadata(state: ProjectState) -> HarmonyProjectMetadata:
    notes = {note.id: note for note in state.notes}
    groups = (
        HarmonyGroup(
            group_id="managed-group",
            measure_index=0,
            start_tick=0,
            duration_ticks=1920,
            chord_symbol="C",
            voicing_pitches=(48, 55, 60),
            generated_note_ids=("generated-c", "generated-g"),
            original_signatures={
                note_id: NoteSignature.from_note(notes[note_id])
                for note_id in ("generated-c", "generated-g")
            },
            detached=False,
            application_origin="auto",
            replace_policy="automatic",
        ),
        HarmonyGroup(
            group_id="detached-group",
            measure_index=0,
            start_tick=0,
            duration_ticks=1920,
            chord_symbol="E",
            voicing_pitches=(52, 56, 59),
            generated_note_ids=("generated-e",),
            original_signatures={
                "generated-e": NoteSignature.from_note(notes["generated-e"])
            },
            detached=True,
            application_origin="auto",
            replace_policy="automatic",
        ),
    )
    return HarmonyProjectMetadata(groups)


def _controller_with_harmony() -> EditorController:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    controller._execute_harmony_transaction(state, metadata)
    return controller


def _fail_recovery_timer_start(
    controller: EditorController,
    monkeypatch,
    message: str = "recovery timer unavailable",
) -> None:
    def fail() -> None:
        raise OSError(message)

    monkeypatch.setattr(controller._recovery_timer, "start", fail)


def _visible_model_snapshot(controller: EditorController) -> tuple[tuple[object, ...], ...]:
    model = controller.noteModel
    roles = tuple(sorted(model.roleNames()))
    return tuple(
        tuple(model.data(model.index(row, 0), role) for role in roles)
        for row in range(model.rowCount())
    )


def _atomic_snapshot(controller: EditorController) -> dict[str, object]:
    return {
        "state": controller.state,
        "metadata": controller.harmony_metadata,
        "history_undo": tuple(controller._history._undo),
        "history_redo": tuple(controller._history._redo),
        "metadata_undo": tuple(controller._harmony_history._undo),
        "metadata_redo": tuple(controller._harmony_history._redo),
        "history_undo_depth": controller._history.undo_depth,
        "history_redo_depth": len(controller._history._redo),
        "metadata_undo_depth": len(controller._harmony_history._undo),
        "metadata_redo_depth": len(controller._harmony_history._redo),
        "visible_model": _visible_model_snapshot(controller),
        "project_session_id": controller.projectSessionId,
        "saved_content": controller._saved_content,
        "current_path": controller._current_path,
        "gesture_origin": controller._gesture_origin,
        "gesture_dx": controller._gesture_dx,
        "gesture_dy": controller._gesture_dy,
        "draw_note_id": controller._draw_note_id,
        "draw_start_time": controller._draw_start_time,
        "sync_state": controller._sync_state,
        "sync_timer_active": controller._sync_timer.isActive(),
        "autosave_timer_active": controller._autosave_timer.isActive(),
        "recovery_timer_active": controller._recovery_timer.isActive(),
    }


def test_harmony_transaction_refreshes_once_then_undo_redo_restore_pair(qtbot) -> None:
    controller = EditorController(ProjectState())
    history = controller._history
    state, metadata = _transaction()
    refreshes: list[object] = []
    controller.stateChanged.connect(lambda: refreshes.append(object()))

    controller._execute_harmony_transaction(state, metadata)

    assert controller._history is history
    assert controller.state == state
    assert controller.harmony_metadata == metadata
    assert len(refreshes) == 1
    controller.undo()
    assert controller.state == ProjectState()
    assert controller.harmony_metadata == HarmonyProjectMetadata.empty()
    controller.redo()
    assert controller.state == state
    assert controller.harmony_metadata == metadata


def test_failed_harmony_transaction_logs_and_keeps_pair_without_refresh(caplog) -> None:
    controller = EditorController(ProjectState())
    state, _ = _transaction()
    refreshes: list[object] = []
    controller.stateChanged.connect(lambda: refreshes.append(object()))

    with caplog.at_level("ERROR"):
        controller._execute_harmony_transaction(state, object())  # type: ignore[arg-type]

    assert controller.state == ProjectState()
    assert controller.harmony_metadata == HarmonyProjectMetadata.empty()
    assert refreshes == []
    assert "harmony transaction failed" in caplog.text.lower()


def test_refresh_failure_rolls_back_harmony_transaction_exactly(
    qtbot, caplog, monkeypatch
) -> None:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    before = _atomic_snapshot(controller)
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"):
        controller._execute_harmony_transaction(state, metadata)

    assert _atomic_snapshot(controller) == before
    assert "harmony transaction failed" in caplog.text.lower()


def test_apply_harmony_transaction_failure_emits_feedback_by_default(
    qtbot, caplog, monkeypatch
) -> None:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    before = _atomic_snapshot(controller)
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"):
        committed = controller.apply_harmony_transaction(state, metadata)

    assert committed is False
    assert _atomic_snapshot(controller) == before
    assert feedback == ["和弦应用失败，请重试"]


def test_apply_harmony_transaction_failure_feedback_can_be_suppressed(
    qtbot, caplog, monkeypatch
) -> None:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    before = _atomic_snapshot(controller)
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"):
        committed = controller.apply_harmony_transaction(
            state, metadata, emit_failure_feedback=False
        )

    assert committed is False
    assert _atomic_snapshot(controller) == before
    assert feedback == []


@pytest.mark.parametrize("transaction_kind", ("ordinary", "harmony"))
def test_late_refresh_failure_does_not_mutate_unnamed_draft_path(
    transaction_kind, qtbot, caplog, monkeypatch
) -> None:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    before = _atomic_snapshot(controller)
    path_changes: list[str | None] = []
    controller.currentFileChanged.connect(
        lambda: path_changes.append(controller._current_path)
    )

    _fail_recovery_timer_start(
        controller, monkeypatch, "recovery timer unavailable after transition"
    )

    with caplog.at_level("ERROR"):
        if transaction_kind == "ordinary":
            controller._execute(state)
            expected_log = "editor operation failed"
        else:
            controller._execute_harmony_transaction(state, metadata)
            expected_log = "harmony transaction failed"

    assert path_changes == []
    assert _atomic_snapshot(controller) == before
    assert expected_log in caplog.text.lower()
    assert "recovery timer unavailable after transition" in caplog.text.lower()


def test_secondary_model_restore_failure_is_logged_without_losing_history_pair(
    qtbot, caplog, monkeypatch
) -> None:
    controller = EditorController(ProjectState())
    state, metadata = _transaction()
    history_before = (
        controller.state,
        controller.harmony_metadata,
        tuple(controller._history._undo),
        tuple(controller._history._redo),
        tuple(controller._harmony_history._undo),
        tuple(controller._harmony_history._redo),
    )
    original_set_state = controller._model.set_state
    set_state_calls = 0

    def fail_during_restore(next_state: ProjectState) -> None:
        nonlocal set_state_calls
        set_state_calls += 1
        if set_state_calls == 2:
            raise RuntimeError("visible rollback unavailable")
        original_set_state(next_state)

    monkeypatch.setattr(controller._model, "set_state", fail_during_restore)
    _fail_recovery_timer_start(controller, monkeypatch)
    feedback: list[str] = []
    controller.feedbackRequested.connect(feedback.append)

    with caplog.at_level("ERROR"):
        controller._execute_harmony_transaction(state, metadata)

    assert (
        controller.state,
        controller.harmony_metadata,
        tuple(controller._history._undo),
        tuple(controller._history._redo),
        tuple(controller._harmony_history._undo),
        tuple(controller._harmony_history._redo),
    ) == history_before
    assert feedback == ["和弦应用失败，请重试"]
    assert "harmony transaction failed" in caplog.text.lower()
    assert "recovery timer unavailable" in caplog.text.lower()
    assert "editor rollback model restore failed" in caplog.text.lower()
    assert "visible rollback unavailable" in caplog.text.lower()


def test_refresh_failure_rolls_back_undo_exactly(
    qtbot, tmp_path, caplog, monkeypatch
) -> None:
    controller = EditorController(
        ProjectState(), project_library=ProjectLibrary(tmp_path / "working-library")
    )
    controller._set_current_path(str(tmp_path / "existing-project.pianoai.json"))
    state, metadata = _transaction()
    controller._execute_harmony_transaction(state, metadata)
    controller._saved_content = state.content
    controller._set_current_path(None)
    before = _atomic_snapshot(controller)
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"):
        controller.undo()

    assert _atomic_snapshot(controller) == before
    assert "undo failed" in caplog.text.lower()


def test_refresh_failure_rolls_back_redo_exactly(
    qtbot, tmp_path, caplog, monkeypatch
) -> None:
    controller = EditorController(
        ProjectState(), project_library=ProjectLibrary(tmp_path / "working-library")
    )
    controller._set_current_path(str(tmp_path / "existing-project.pianoai.json"))
    state, metadata = _transaction()
    controller._execute_harmony_transaction(state, metadata)
    controller.undo()
    controller._saved_content = controller.state.content
    controller._set_current_path(None)
    before = _atomic_snapshot(controller)
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"):
        controller.redo()

    assert _atomic_snapshot(controller) == before
    assert "redo failed" in caplog.text.lower()


def test_refresh_failure_rolls_back_project_replacement_exactly(
    qtbot, tmp_path, caplog, monkeypatch
) -> None:
    controller = EditorController(
        ProjectState(), project_library=ProjectLibrary(tmp_path / "working-library")
    )
    controller._set_current_path(str(tmp_path / "existing-project.pianoai.json"))
    state, metadata = _transaction()
    controller._execute_harmony_transaction(state, metadata)
    manual = Note.create(id="manual-c", pitch=60, start_time=2.0, duration=0.5)
    controller._execute(controller.state.add_note(manual, select=False))
    controller.undo()
    controller._saved_content = controller.state.content
    controller._set_current_path(None)
    replacement = ProjectState(
        (Note.create(id="replacement", pitch=72, start_time=0.0, duration=0.5),)
    )
    replacement_path = str(tmp_path / "replacement-project.pianoai.json")
    before = _atomic_snapshot(controller)
    path_changes: list[str | None] = []
    controller.currentFileChanged.connect(
        lambda: path_changes.append(controller._current_path)
    )
    _fail_recovery_timer_start(controller, monkeypatch)

    with caplog.at_level("ERROR"), pytest.raises(OSError):
        controller._replace_project(
            replacement, saved_content=None, path=replacement_path
        )

    assert _atomic_snapshot(controller) == before
    assert path_changes == [replacement_path, None]
    assert controller.currentFilePath == ""
    assert "project replacement failed" in caplog.text.lower()


def test_project_replace_resets_metadata_without_replacing_history() -> None:
    controller = _controller_with_harmony()
    history = controller._history

    controller.newProject()

    assert controller._history is history
    assert controller.state == ProjectState()
    assert controller.harmony_metadata == HarmonyProjectMetadata.empty()
    assert controller.canUndo is False


def test_discard_restores_saved_harmony_transaction_without_undo(qtbot, tmp_path) -> None:
    controller = _controller_with_harmony()
    saved_state = controller.state
    saved_metadata = controller.harmony_metadata
    target = tmp_path / "saved-harmony.pianoai.json"
    with qtbot.waitSignal(controller.saveSucceeded, timeout=2_000):
        controller.saveProject(str(target))
    replacement = ProjectState(
        (Note.create(id="replacement", pitch=72, start_time=2.0, duration=0.5),)
    )

    assert controller.apply_harmony_transaction(
        replacement, HarmonyProjectMetadata.empty()
    ) is True
    assert controller.isDirty is True
    assert controller.discardUnsavedChanges() is True

    assert controller.state == saved_state
    assert controller.harmony_metadata == saved_metadata
    assert controller.isDirty is False
    assert controller.canUndo is False


def test_move_gesture_detaches_generated_group_and_undo_restores_it() -> None:
    controller = _controller_with_harmony()
    controller.selectNotes(["generated-c"])

    controller.beginGesture()
    controller.previewMovePixels(80.0, 18.0)
    controller.commitGesture()

    assert controller.harmony_metadata.groups[0].detached is True
    controller.undo()
    assert controller.harmony_metadata.groups[0].detached is False


@pytest.mark.parametrize(
    ("field", "value"),
    (("pitch", 49), ("start_time", 0.25), ("duration", 0.5), ("velocity", 90)),
)
def test_regular_generated_note_property_edit_detaches_group(field, value) -> None:
    controller = _controller_with_harmony()

    controller._execute(controller.state.update_note("generated-c", **{field: value}))

    assert controller.harmony_metadata.groups[0].detached is True


def test_partial_group_deletion_immediately_keeps_remaining_metadata_detached(qtbot) -> None:
    controller = _controller_with_harmony()
    controller.selectNotes(["generated-c"])
    controller.deleteSelected()

    groups = controller.harmony_metadata.groups
    assert [group.generated_note_ids for group in groups] == [
        ("generated-e", "generated-g"),
    ]
    assert all(group.detached for group in groups)


def test_deleting_entire_generated_group_immediately_cleans_metadata(qtbot) -> None:
    controller = _controller_with_harmony()
    controller.selectNotes(["generated-c", "generated-e", "generated-g"])
    controller.deleteSelected()

    assert controller.harmony_metadata == HarmonyProjectMetadata.empty()


def test_manual_same_range_pitch_class_note_detaches_matching_group() -> None:
    controller = _controller_with_harmony()
    manual = Note.create(id="manual-c", pitch=60, start_time=0.5, duration=0.25)

    controller._execute(controller.state.add_note(manual, select=False))

    assert controller.harmony_metadata.groups[0].detached is True


def test_analysis_snapshot_excludes_managed_and_detached_generated_notes(qtbot, tmp_path) -> None:
    generated_state, _ = _transaction()
    metadata = _managed_and_detached_metadata(generated_state)
    user_note = Note.create(id="user-melody", pitch=72, start_time=1.0, duration=0.5)
    editor = EditorController(ProjectState())
    editor._execute_harmony_transaction(
        generated_state.add_note(user_note, select=False), metadata
    )
    settings = HarmonySettings(
        settings=QSettings(str(tmp_path / "harmony.ini"), QSettings.Format.IniFormat)
    )
    settings.setMode("preview")
    controller = HarmonySuggestionController(editor, settings)
    try:
        request = controller._snapshot_request(include_manual_tail=True)
    finally:
        controller.close()

    assert tuple(note.note_id for note in request.melody) == ("user-melody",)
