from __future__ import annotations

import pytest

from ai_piano_studio.domain.history import History
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.harmony.history_sync import HarmonyHistoryCoordinator
from ai_piano_studio.harmony.project_metadata import (
    HarmonyGroup,
    HarmonyProjectMetadata,
    NoteSignature,
)


def _harmony_transaction() -> tuple[ProjectState, HarmonyProjectMetadata]:
    notes = tuple(
        Note.create(
            id=note_id,
            pitch=pitch,
            start_time=0.0,
            duration=1.0,
            velocity=72,
        )
        for note_id, pitch in (("generated-c", 48), ("generated-e", 52), ("generated-g", 55))
    )
    state = ProjectState(notes)
    group = HarmonyGroup(
        group_id="group-c-major",
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
    )
    return state, HarmonyProjectMetadata((group,))


def _single_note_transaction(
    note_id: str, pitch: int
) -> tuple[ProjectState, HarmonyProjectMetadata]:
    note = Note.create(
        id=note_id,
        pitch=pitch,
        start_time=0.0,
        duration=1.0,
        velocity=72,
    )
    state = ProjectState((note,))
    group = HarmonyGroup(
        group_id=f"group-{note_id}",
        measure_index=0,
        start_tick=0,
        duration_ticks=1920,
        chord_symbol="C",
        voicing_pitches=(pitch, pitch + 4, pitch + 7),
        generated_note_ids=(note.id,),
        original_signatures={note.id: NoteSignature.from_note(note)},
        detached=False,
        application_origin="auto",
        replace_policy="automatic",
    )
    return state, HarmonyProjectMetadata((group,))


def test_harmony_execute_undo_and_redo_restore_state_and_metadata_together() -> None:
    history = History(ProjectState())
    coordinator = HarmonyHistoryCoordinator(history)
    state, metadata = _harmony_transaction()

    coordinator.execute(state, metadata)

    assert tuple(note.id for note in history.state.notes) == (
        "generated-c",
        "generated-e",
        "generated-g",
    )
    assert coordinator.metadata.groups[0].generated_note_ids == (
        "generated-c",
        "generated-e",
        "generated-g",
    )
    assert coordinator.undo() == (ProjectState(), HarmonyProjectMetadata.empty())
    restored_state, restored_metadata = coordinator.redo()
    assert tuple(note.id for note in restored_state.notes) == (
        "generated-c",
        "generated-e",
        "generated-g",
    )
    assert restored_state == state
    assert {
        note.id: NoteSignature.from_note(note) for note in restored_state.notes
    } == dict(metadata.groups[0].original_signatures)
    assert restored_metadata == metadata


def test_regular_execute_after_undo_clears_state_and_metadata_redo() -> None:
    history = History(ProjectState())
    coordinator = HarmonyHistoryCoordinator(history)
    generated_state, metadata = _harmony_transaction()
    coordinator.execute(generated_state, metadata)
    coordinator.undo()
    manual = Note.create(id="manual", pitch=60, start_time=2.0, duration=0.5)

    coordinator.execute(ProjectState((manual,)))
    state_after_redo, metadata_after_redo = coordinator.redo()

    assert history.can_redo is False
    assert tuple(note.id for note in state_after_redo.notes) == ("manual",)
    assert metadata_after_redo == HarmonyProjectMetadata.empty()


def test_content_unchanged_execute_does_not_create_an_undo_entry() -> None:
    state, metadata = _single_note_transaction("generated", 48)
    history = History(state)
    coordinator = HarmonyHistoryCoordinator(history, metadata)

    coordinator.execute(state.select_ids({"generated"}))

    assert history.undo_depth == 0
    assert coordinator.metadata == metadata


def test_cancel_gesture_restores_original_state_and_metadata_without_undo() -> None:
    state, metadata = _single_note_transaction("generated", 48)
    history = History(state)
    coordinator = HarmonyHistoryCoordinator(history, metadata)
    coordinator.begin_gesture()
    coordinator.preview(state.update_note("generated", pitch=49))
    assert coordinator.metadata.groups[0].detached is True

    restored_state, restored_metadata = coordinator.cancel_gesture()

    assert restored_state == state
    assert restored_metadata == metadata
    assert history.undo_depth == 0


def test_commit_gesture_creates_one_synchronized_undo_entry() -> None:
    state, metadata = _single_note_transaction("generated", 48)
    history = History(state)
    coordinator = HarmonyHistoryCoordinator(history, metadata)
    coordinator.begin_gesture()
    coordinator.preview(state.update_note("generated", pitch=49))

    committed_state, committed_metadata = coordinator.commit_gesture()

    assert committed_state.notes[0].pitch == 49
    assert committed_metadata.groups[0].detached is True
    assert history.undo_depth == 1
    assert coordinator.undo() == (state, metadata)


def test_replace_current_updates_pair_without_adding_a_user_undo_item() -> None:
    state, metadata = _single_note_transaction("generated", 48)
    history = History(state)
    coordinator = HarmonyHistoryCoordinator(history, metadata)

    replaced_state, replaced_metadata = coordinator.replace_current(state.synchronize())

    assert replaced_state.playback_notes == replaced_state.notes
    assert replaced_metadata == metadata
    assert history.undo_depth == 0


def test_limit_trims_state_and_metadata_history_at_the_same_boundary() -> None:
    history = History(ProjectState(), limit=2)
    coordinator = HarmonyHistoryCoordinator(history, limit=2)
    transactions = (
        _single_note_transaction("generated-1", 48),
        _single_note_transaction("generated-2", 50),
        _single_note_transaction("generated-3", 52),
    )
    for state, metadata in transactions:
        coordinator.execute(state, metadata)

    assert coordinator.undo() == transactions[1]
    assert coordinator.undo() == transactions[0]
    assert coordinator.undo() == transactions[0]


def test_reset_preserves_wrapped_history_identity_and_clears_both_stacks() -> None:
    history = History(ProjectState())
    coordinator = HarmonyHistoryCoordinator(history)
    state, metadata = _single_note_transaction("generated", 48)
    coordinator.execute(state, metadata)

    reset_state, reset_metadata = coordinator.reset(ProjectState())

    assert reset_state == ProjectState()
    assert reset_metadata == HarmonyProjectMetadata.empty()
    assert history.state == ProjectState()
    assert history.can_undo is False
    assert history.can_redo is False


def test_invalid_metadata_is_logged_before_state_or_metadata_changes(caplog) -> None:
    history = History(ProjectState())
    coordinator = HarmonyHistoryCoordinator(history)
    state, _ = _single_note_transaction("generated", 48)

    with caplog.at_level("ERROR"):
        try:
            coordinator.execute(state, object())  # type: ignore[arg-type]
        except AttributeError:
            pass
        else:
            raise AssertionError("invalid metadata must fail")

    assert history.state == ProjectState()
    assert coordinator.metadata == HarmonyProjectMetadata.empty()
    assert "harmony history execute failed" in caplog.text.lower()


def test_invalid_initial_metadata_is_logged_without_changing_history(caplog) -> None:
    history = History(ProjectState())

    with caplog.at_level("ERROR"), pytest.raises(AttributeError):
        HarmonyHistoryCoordinator(history, object())  # type: ignore[arg-type]

    assert history.state == ProjectState()
    assert history.can_undo is False
    assert "harmony history initialization failed" in caplog.text.lower()


def test_mismatched_limit_is_rejected_without_changing_wrapped_history(caplog) -> None:
    history = History(ProjectState(), limit=2)

    with caplog.at_level("ERROR"), pytest.raises(ValueError, match="limit"):
        HarmonyHistoryCoordinator(history)

    states = tuple(
        _single_note_transaction(f"generated-{index}", 47 + index)[0]
        for index in range(1, 4)
    )
    for state in states:
        history.execute(state)
    assert history.undo() == states[1]
    assert history.undo() == states[0]
    assert history.undo() == states[0]
    assert "initialization failed" in caplog.text.lower()
