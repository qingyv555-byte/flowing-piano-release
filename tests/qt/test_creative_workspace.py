from ai_piano_studio.creative.workspace import CreativeWorkspace
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.persistence.project_document import load_project_document


def stage(workspace):
    context = workspace.gateway.create_execution_context(
        authorized_bars=(BarRange(1, 4),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    assert workspace.gateway.execute('add_notes', {'notes': [
        {'pitch': 60, 'start_time': 0, 'duration': .5, 'velocity': 70}
    ]}, context).success
    workspace.proposals.register(context)


def test_isolated_proposal_save_reopen_and_one_undo(qapp, tmp_path):
    workspace = CreativeWorkspace()
    try:
        stage(workspace)
        assert not workspace.editor.state.notes
        assert workspace.proposals.apply_proposal().success
        target = tmp_path / 'theme.pianoai.json'
        workspace.save(target)
        assert load_project_document(target).state.notes == workspace.editor.state.notes
        workspace.editor.undo()
        assert not workspace.editor.state.notes
    finally:
        workspace.close()


def test_cancel_discards_proposal_and_invalidates_late_result(qapp):
    workspace = CreativeWorkspace()
    try:
        epoch = workspace.begin()
        stage(workspace)
        workspace.cancel()
        assert not workspace.is_current(epoch)
        assert not workspace.proposals.apply_proposal().success
        assert not workspace.editor.state.notes
        assert workspace.is_current(workspace.begin())
    finally:
        workspace.close()
