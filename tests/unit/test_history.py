import pytest

from ai_piano_studio.domain.history import History
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.music_layer import MusicLayer
from ai_piano_studio.domain.project import ProjectState


def initial_state() -> ProjectState:
    note = Note.create(id="a", pitch=60, start_time=0.0, duration=0.5)
    return ProjectState(notes=(note,), selected_ids=frozenset({"a"}))


def test_execute_supports_undo_and_redo() -> None:
    history = History(initial_state())
    changed = history.state.update_selected(velocity=100)

    history.execute(changed)

    assert history.can_undo
    assert history.undo().selected_notes[0].velocity == 75
    assert history.redo().selected_notes[0].velocity == 100


def test_selection_only_change_does_not_create_undo_entry() -> None:
    history = History(initial_state())

    history.execute(history.state.clear_selection())

    assert history.state.selected_ids == frozenset()
    assert history.undo_depth == 0


def test_new_edit_clears_redo() -> None:
    history = History(initial_state())
    history.execute(history.state.update_selected(velocity=90))
    history.undo()
    history.execute(history.state.update_selected(velocity=80))

    assert not history.can_redo


def test_drag_preview_commits_one_undo_entry() -> None:
    history = History(initial_state())
    history.begin_gesture()
    history.preview(history.state.move_selected(delta_time=0.1, delta_pitch=0))
    history.preview(history.state.move_selected(delta_time=0.2, delta_pitch=0))
    history.commit_gesture()

    assert history.undo_depth == 1
    assert history.state.selected_notes[0].start_time == pytest.approx(0.3)
    assert history.undo().selected_notes[0].start_time == 0.0


def test_cancel_gesture_restores_original_state() -> None:
    history = History(initial_state())
    history.begin_gesture()
    history.preview(history.state.move_selected(delta_time=1.0, delta_pitch=2))

    restored = history.cancel_gesture()

    assert restored.selected_notes[0].start_time == 0.0
    assert restored.selected_notes[0].pitch == 60
    assert history.undo_depth == 0


def test_music_layer_change_is_part_of_undoable_content() -> None:
    history = History(initial_state())
    changed = history.state.add_layer(
        MusicLayer.create(id="melody", name="主旋律", role="melody")
    )

    history.execute(changed)

    assert history.undo_depth == 1
    assert [layer.id for layer in history.undo().layers] == ["layer-default"]
    assert [layer.id for layer in history.redo().layers] == ["layer-default", "melody"]
