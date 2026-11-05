from __future__ import annotations

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController


def test_agent_turn_commits_multiple_previews_as_one_undo_entry() -> None:
    """Break caught: one agent turn becomes several Ctrl+Z operations."""
    editor = EditorController()
    baseline_revision = editor.projectRevision

    editor.begin_agent_turn()
    editor.preview_agent_state(
        editor.state.add_note(
            Note.create(id="agent-c", pitch=60, start_time=0.0, duration=0.5),
            select=False,
        )
    )
    editor.preview_agent_state(
        editor.state.add_note(
            Note.create(id="agent-e", pitch=64, start_time=0.5, duration=0.5),
            select=False,
        )
    )
    editor.commit_agent_turn()

    assert {note.id for note in editor.state.active_notes} == {"agent-c", "agent-e"}
    assert editor._history.undo_depth == 1
    assert editor.projectRevision == baseline_revision + 1

    editor.undo()

    assert editor.state.active_notes == ()


def test_agent_note_preview_does_not_steal_existing_selection() -> None:
    existing = Note.create(id="existing", pitch=60, start_time=0.0, duration=0.5)
    editor = EditorController(ProjectState((existing,), frozenset({existing.id})))

    editor.begin_agent_turn()
    editor.preview_agent_state(
        editor.state.add_note(
            Note.create(id="agent-c", pitch=64, start_time=1.0, duration=0.5),
            select=False,
        )
    )
    editor.commit_agent_turn()

    assert editor.state.selected_ids == frozenset({existing.id})


def test_cancelled_agent_turn_restores_state_history_and_revision() -> None:
    """Break caught: a failed turn leaks preview state or leaves an undo entry behind."""
    editor = EditorController()
    before = (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision)

    editor.begin_agent_turn()
    editor.preview_agent_state(editor.state.with_tempo(TempoMap(bpm=100)))
    editor.cancel_agent_turn()

    assert (editor.state, editor.harmony_metadata, editor._history.undo_depth, editor.projectRevision) == before


def test_agent_turn_rejects_nested_and_inactive_state_previews() -> None:
    """Break caught: two callers can interleave controller transactions."""
    editor = EditorController()

    with pytest.raises(RuntimeError, match="no agent turn"):
        editor.preview_agent_state(editor.state)

    editor.begin_agent_turn()
    with pytest.raises(RuntimeError, match="already active"):
        editor.begin_agent_turn()
    editor.cancel_agent_turn()


def test_project_revision_advances_for_an_ordinary_durable_edit() -> None:
    """Break caught: a proposal cannot detect an intervening user edit."""
    editor = EditorController()

    editor._execute(editor.state.with_tempo(TempoMap(bpm=100)))

    assert editor.projectRevision == 1
