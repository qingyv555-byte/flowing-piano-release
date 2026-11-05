from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from PySide6.QtTest import QSignalSpy


def two_note_controller() -> EditorController:
    notes = (
        Note.create(id="a", pitch=60, start_time=0.0, duration=0.5),
        Note.create(id="b", pitch=64, start_time=1.0, duration=0.5),
    )
    return EditorController(ProjectState(notes, frozenset({"a"})))


def test_pixel_drag_changes_time_and_pitch_in_one_undo_entry() -> None:
    controller = two_note_controller()
    controller.beginGesture()
    controller.previewMovePixels(40, -9)
    controller.previewMovePixels(40, -9)
    controller.commitGesture()
    note = controller.state.selected_notes[0]
    assert (note.start_time, note.pitch) == (1.0, 61)
    controller.undo()
    assert (controller.state.selected_notes[0].start_time,
            controller.state.selected_notes[0].pitch) == (0.0, 60)


def test_pixel_resize_uses_quarter_second_snap() -> None:
    controller = two_note_controller()
    controller.beginGesture()
    controller.previewResizePixels(20)
    controller.commitGesture()
    assert controller.state.selected_notes[0].duration == 0.75


def test_pixel_drag_snaps_off_grid_delta_to_snap_step() -> None:
    controller = two_note_controller()
    controller.beginGesture()
    controller.previewMovePixels(30, 0)  # 0.375s -> 0.5s on a 0.25s grid
    controller.commitGesture()
    assert controller.state.selected_notes[0].start_time == 0.5


def test_pixel_drag_snap_step_follows_project_bpm() -> None:
    controller = two_note_controller()
    controller.setBpm(60)  # 1/8 拍 = 0.5s
    controller.beginGesture()
    controller.previewMovePixels(60, 0)  # 0.75s -> 1.0s on a 0.5s grid
    controller.commitGesture()
    assert controller.state.selected_notes[0].start_time == 1.0


def test_pixel_resize_snaps_off_grid_delta_to_snap_step() -> None:
    controller = two_note_controller()
    controller.beginGesture()
    controller.previewResizePixels(25)  # 0.3125s -> 0.25s on a 0.25s grid
    controller.commitGesture()
    assert controller.state.selected_notes[0].duration == 0.75


def test_pixel_resize_without_snap_keeps_raw_delta() -> None:
    controller = two_note_controller()
    controller.setSnapEnabled(False)
    controller.beginGesture()
    controller.previewResizePixels(10)  # 0.125s
    controller.commitGesture()
    assert controller.state.selected_notes[0].duration == 0.625


def test_draw_gesture_creates_a_snap_aligned_note_with_drag_duration() -> None:
    controller = EditorController(ProjectState())

    controller.beginDrawNoteAt(20, 18)
    controller.previewDrawNoteTo(100)
    controller.commitGesture()

    note = controller.state.notes[0]
    assert (note.pitch, note.start_time, note.duration) == (71, 0.25, 1.0)


def test_pitch_range_clamps_blank_canvas_creation_to_its_lowest_note() -> None:
    controller = EditorController(ProjectState())

    controller.setPitchRange(84, 36)
    controller.createNoteAt(0, 9_999)

    assert controller.state.notes[0].pitch == 36


def test_pixel_drag_can_move_note_left() -> None:
    controller = two_note_controller()
    controller.selectNote("b", False)
    controller.beginGesture()
    controller.previewMovePixels(-40, 0)
    controller.commitGesture()
    assert controller.state.selected_notes[0].start_time == 0.5


def test_marquee_selection_replaces_or_extends_selection() -> None:
    controller = two_note_controller()
    controller.selectRect(0, 140, 121, 95, False)
    assert controller.state.selected_ids == frozenset({"a", "b"})
    controller.selectRect(0, 210, 41, 25, False)
    assert controller.state.selected_ids == frozenset({"a"})


def test_selection_and_drag_do_not_reset_note_delegates() -> None:
    controller = two_note_controller()
    reset_spy = QSignalSpy(controller.noteModel.modelReset)
    controller.selectNote("b", False)
    controller.beginGesture()
    controller.previewMovePixels(20, 0)
    controller.commitGesture()
    assert reset_spy.count() == 0
