from dataclasses import replace
import pytest
from ai_piano_studio.creative.workspace import CreativeWorkspace
from ai_piano_studio.creative.studio_apply import append_theme
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState


def test_append_preserves_original_and_undo_restores_exact_state(qapp):
    workspace = CreativeWorkspace()
    editor = workspace.editor
    try:
        original = ProjectState(notes=(Note.create(pitch=48, start_time=0, duration=2),))
        editor.begin_agent_turn()
        editor.preview_agent_state(original)
        editor.commit_agent_turn()
        before = editor.state
        theme = ProjectState(notes=(Note.create(pitch=72, start_time=.5, duration=1),))
        append_theme(editor, theme, lambda: False)
        assert editor.state.notes[0] == before.notes[0]
        assert editor.state.notes[-1].start_time == 2.5
        assert editor.state.notes[-1].id != theme.notes[0].id
        assert editor.state.tempo == before.tempo
        assert len(editor.state.layers) == len(before.layers) + 1
        editor.undo()
        assert editor.state == before
        with pytest.raises(ValueError):
            append_theme(editor, theme, lambda: True)
        assert editor.state == before
    finally:
        workspace.close()
