"""Offline rendered measurements use staged notes without starting audio hardware."""
from array import array
import json
from threading import current_thread, main_thread

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.audition_tools import analyze_audition, audition_snapshot
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.providers import ProviderEvent
from ai_piano_studio.music_agent.song_resolver import SourceCancelled
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from tests.music_agent.test_creation_workflow import done
from tests.music_agent.test_source_confirmation_workflow import call
from tests.music_agent.test_studio_model_assist import studio_assistant


def gateway_context():
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.1),)))
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}))
    return editor, gateway, context


def test_rendered_analysis_includes_staged_notes_and_preserves_editor():
    editor, gateway, context = gateway_context()
    before = editor.state.content, editor.historyUndoDepth
    assert gateway.execute('add_notes', dict(notes=[dict(pitch=48, start_time=0, duration=.1, velocity=70)]), context).success
    snapshot = audition_snapshot(gateway, dict(start_bar=1, end_bar=1), context)
    result = analyze_audition(snapshot)
    assert result.success and result.data['audible']
    assert result.data['note_count'] == 2 and result.data['max_polyphony'] == 2
    assert result.data['min_pitch'] == 48 and result.data['max_pitch'] == 60
    assert result.data['peak_dbfs'] <= 0 and result.data['rms_dbfs'] <= result.data['peak_dbfs']
    assert result.data['source'] == 'built_in_piano_analysis'
    assert (editor.state.content, editor.historyUndoDepth) == before
    assert json.loads(serialize_provider_feedback(result))['data']['analysis_kind'] == 'rendered_pcm_statistics'
    gateway.release_proposal(context)


def test_analysis_rejects_outside_scope_and_preserves_permission_boundary():
    _editor, gateway, context = gateway_context()
    result = audition_snapshot(gateway, dict(start_bar=2, end_bar=2), context)
    assert not result.success and result.error_code == 'permission_denied'


def test_signal_statistics_measure_actual_pcm_and_cancel_without_playback():
    class Renderer:
        output_channels = 1
        export_sample_rate = 4
        def render_project_cancellable(self, notes, cancelled):
            return array('h', [0, 32767, -32768, 1000])
    snapshot = dict(notes=(), renderer=Renderer(), gain=1.0, source='controlled_pcm',
                    start_bar=1, end_bar=1, start_seconds=0, end_seconds=1)
    result = analyze_audition(snapshot)
    assert result.data['clipped_sample_count'] == 2
    assert result.data['rendered_seconds'] == 1
    with pytest.raises(SourceCancelled):
        analyze_audition(snapshot, lambda: True)


def test_model_analyzes_staged_proposal_on_worker_without_starting_speakers(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent import audition_tools
    threads = []
    original = audition_tools.render_project
    def render(*args, **kwargs):
        threads.append(current_thread())
        return original(*args, **kwargs)
    monkeypatch.setattr(audition_tools, 'render_project', render)
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=.1),)))
    before = editor.state.content, editor.historyUndoDepth
    assistant, provider, playback = studio_assistant(tmp_path, editor, [
        call('add_notes', dict(notes=[dict(pitch=48, start_time=0, duration=.1, velocity=70)])),
        call('analyze_audition', dict(start_bar=1, end_bar=1)),
        (ProviderEvent(text_delta='已检查声音信号和音符密度，可以试听。', finish_reason='stop'),),
    ])
    try:
        assert assistant.sendMessage('加个低音，检查一下试听信号')
        done(qapp, assistant)
        assert assistant.lastTurnResult['status'] == 'proposal', assistant.lastTurnResult
        results = [json.loads(m['content']) for request in provider.requests for m in request
                   if m['role'] == 'tool' and 'rendered_pcm_statistics' in m['content']]
        assert results and results[-1]['data']['note_count'] == 2
        assert threads and all(t is not main_thread() for t in threads)
        assert not playback.notes
        assert (editor.state.content, editor.historyUndoDepth) == before
    finally:
        assistant.shutdown()


def test_feedback_cursor_recovers_all_notes_under_small_byte_budget():
    notes = tuple(Note.create(id=f'n{i}', pitch=60, start_time=0, duration=.1) for i in range(60))
    gateway = MusicAgentToolGateway(EditorController(ProjectState(notes=notes)))
    context = gateway.create_execution_context(authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES}))
    offset, ids = 0, []
    while offset is not None:
        result = gateway.execute('get_notes', dict(start_bar=1, end_bar=1, offset=offset), context)
        assert result.success
        data = json.loads(serialize_provider_feedback(result, max_bytes=1024))['data']
        ids.extend(note['id'] for note in data['notes'])
        assert data['next_offset'] is None or data['next_offset'] > offset
        offset = data['next_offset']
    assert ids == [n.id for n in notes]
