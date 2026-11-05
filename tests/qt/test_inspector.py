from PySide6.QtTest import QSignalSpy

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController


def two_selected_note_controller() -> EditorController:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.5, velocity=70),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.5, velocity=90),
    )
    return EditorController(ProjectState(notes, frozenset({"a", "b"})))


def test_selection_summary_reports_shared_and_mixed_values() -> None:
    controller = two_selected_note_controller()
    summary = controller.selectionSummary
    assert summary["count"] == 2
    assert summary["duration"] == 0.5
    assert summary["durationMixed"] is False
    assert summary["pitch"] is None
    assert summary["pitchMixed"] is True
    assert summary["velocity"] is None
    assert summary["velocityMixed"] is True


def test_property_edit_updates_selection_as_one_undoable_change() -> None:
    controller = two_selected_note_controller()
    controller.setSelectedProperty("velocity", 100)
    assert {note.velocity for note in controller.state.selected_notes} == {100}
    controller.undo()
    assert [note.velocity for note in controller.state.selected_notes] == [70, 90]


def test_segment_edit_targets_one_child_after_group_selection() -> None:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.2),
        Note.create(id="b", pitch=60, start_time=0.2, duration=0.2),
    )
    controller = EditorController(ProjectState(notes))

    controller.selectNotes(["b"])
    controller.setSelectedProperty("pitch", 64)

    assert [note.pitch for note in controller.state.notes] == [60, 64]


def test_selected_release_is_one_undoable_edit_and_keeps_duration() -> None:
    controller = two_selected_note_controller()
    before_depth = controller.historyUndoDepth
    before_duration = controller.state.notes[0].duration

    assert controller.setSelectedRelease("custom", 420.0) is True
    assert {note.release_mode for note in controller.state.selected_notes} == {"custom"}
    assert {note.release_ms for note in controller.state.selected_notes} == {420.0}
    assert controller.state.notes[0].duration == before_duration
    assert controller.historyUndoDepth == before_depth + 1

    controller.undo()
    assert {note.release_mode for note in controller.state.selected_notes} == {"natural"}
    assert {note.release_ms for note in controller.state.selected_notes} == {None}


def test_invalid_property_value_keeps_state_and_emits_feedback() -> None:
    controller = two_selected_note_controller()
    before = controller.state
    feedback = QSignalSpy(controller.feedbackRequested)
    controller.setSelectedProperty("duration", 0)
    assert controller.state == before
    assert feedback.count() == 1
