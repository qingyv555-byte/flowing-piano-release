"""Regression cases from the user's assistant workflow report."""
import json

import pytest

from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.response_guard import ResponseCapabilityGuard, ResponseGuardEvidence
from ai_piano_studio.music_agent.provider_feedback import serialize_provider_feedback
from ai_piano_studio.music_agent.tool_contracts import MusicPermission
from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.music_agent.tool_contracts import BarRange
from tests.music_agent.test_capability_integrity import _effective
from tests.music_agent.test_assistant_controller import (
    _SerializedLoopProvider, _serialized_loop_assistant, _wait_until,
)
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.persistence.midi import export_midi, load_midi
from ai_piano_studio.persistence.project_json import load_project


@pytest.mark.parametrize('text,expected', [
    ('我要一段原创钢琴曲', 'CREATE_MUSIC'),
    ('能给我一首温柔的钢琴曲吗？', 'CREATE_MUSIC'),
    ('来首《青花瓷》', 'CREATE_MUSIC'),
    ('请弹一段青花瓷原曲', 'CREATE_MUSIC'),
    ('修改当前旋律', 'EDIT_MUSIC'),
    ('根据已导入的 MIDI 改编', 'EDIT_MUSIC'),
    ('查看已导入的 MIDI', 'READ_MUSIC'),
    ('读取已导入的 MIDI', 'READ_MUSIC'),
    ('帮我找一下青花瓷原曲的 MIDI', 'READ_MUSIC'),
    ('给我一段舒缓的钢琴独奏', 'CREATE_MUSIC'),
    ('重新编配这段旋律', 'EDIT_MUSIC'),
    ('我要青花瓷原曲的介绍', 'CHAT'),
    ('我想了解原创钢琴曲怎么创作', 'CHAT'),
    ('我要修改软件设置', 'CHAT'),
    ('我要一台钢琴', 'CHAT'),
    ('不要给我一段原创钢琴曲', 'CHAT'),
    ('别弹青花瓷原曲', 'CHAT'),
])
def test_natural_language_music_requests(text, expected):
    assert MusicAssistantController._classify_intent(text) == expected


def test_cross_bar_notes_are_readable_but_do_not_gain_write_permission():
    editor = EditorController(ProjectState(notes=tuple(
        Note.create(id=id, pitch=60, start_time=start, duration=duration, velocity=80)
        for id, start, duration in [('left', 1.5, 1), ('right', 3.5, 1),
                                    ('before', 1, 1), ('after', 4, 1)]
    )))
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(authorized_bars=(BarRange(2, 2),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}))
    result = gateway.execute('get_notes', {'start_bar': 2, 'end_bar': 2}, context)
    assert {n['id'] for n in result.data['notes']} == {'left', 'right'}
    assert {n['id'] for n in gateway.compact_context(context, (BarRange(2, 2),))['nearby_notes']} == {'left', 'right'}
    feedback = json.loads(serialize_provider_feedback(result))['data']
    assert feedback['range_semantics'] == 'overlap'
    assert feedback['returned_note_count'] == 2
    denied = gateway.execute('transpose_notes', {'note_ids': ['left'], 'semitones': 2}, context)
    assert not denied.success
    assert editor.state.active_notes[0].pitch == 60


@pytest.mark.parametrize('text', [
    '我没有音乐编辑工具。请提供原曲 MIDI。',
    '当前没有可用的音乐编辑或音频工具。请提供原曲 MIDI。',
    'I cannot edit MIDI notes. Please import a reference MIDI.',
])
def test_false_tool_denial_is_corrected_without_hiding_missing_source(text):
    result = ResponseCapabilityGuard().guard(text, ResponseGuardEvidence(
        intent='CREATE_MUSIC', capabilities=_effective(MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES)))
    assert result.blocked_claims
    assert 'MIDI' in result.text
    assert '音乐编辑工具' not in result.text


def test_truthful_source_and_permission_limitations_are_preserved():
    for text in ['没有原曲 MIDI，无法准确还原。', '当前没有音乐编辑工具。']:
        result = ResponseCapabilityGuard().guard(text, ResponseGuardEvidence(capabilities=_effective()))
        assert result.text == text
        assert not result.blocked_claims


class SilentPreview:
    def __init__(self):
        self.notes = ()

    def play_detached_notes(self, notes):
        self.notes = notes

    def stop_detached_preview(self):
        self.notes = ()


@pytest.mark.parametrize('scenario', ['original', 'edit', 'imported', 'missing_source'])
def test_four_workflows_use_real_gateway_proposal_and_save(tmp_path, qapp, scenario):
    editor = EditorController(ProjectState())
    if scenario in {'edit', 'imported'}:
        source = Note.create(id='source', pitch=60, start_time=0, duration=.5, velocity=70)
        midi = tmp_path / 'reference.mid'
        export_midi(midi, (source,), editor.state.tempo)
        assert editor.importMidiResult(load_midi(midi))
    before = editor.state
    if scenario == 'original':
        instruction = '我要一段原创钢琴曲'
        call = ProviderToolCall('write', 'add_notes', json.dumps({'notes': [
            {'pitch': 64, 'start_time': 0, 'duration': .5, 'velocity': 80}]}))
        expected_intent = 'CREATE_MUSIC'
    elif scenario in {'edit', 'imported'}:
        instruction = '修改当前旋律' if scenario == 'edit' else '根据已导入的 MIDI 改编'
        call = ProviderToolCall('write', 'transpose_notes', json.dumps({
            'note_ids': [before.active_notes[0].id], 'semitones': 2}))
        expected_intent = 'EDIT_MUSIC'
    else:
        instruction = '我要青花瓷原曲，但尚未导入素材'
        call = None
        expected_intent = 'CREATE_MUSIC'
    scripts = [(ProviderEvent(tool_calls=(ProviderToolCall('summary', 'get_project_summary', '{}'),
        ProviderToolCall('read', 'get_notes', '{"start_bar":1,"end_bar":1}'))),)]
    scripts += [(ProviderEvent(tool_calls=(call,)),)] if call else [
        (ProviderEvent(text_delta='没有原曲素材。请先导入参考 MIDI。', finish_reason='stop'),)]
    provider = _SerializedLoopProvider(scripts)
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    preview = SilentPreview()
    assistant._proposal_manager._playback = preview
    assert assistant.sendMessage(instruction)
    _wait_until(qapp, lambda: assistant.assistantStatus in {'idle', 'proposal_ready', 'error'})
    assert assistant.lastTurnResult['intent'] == expected_intent
    assert assistant.lastTurnResult['readCount'] == 2
    assert assistant.lastTurnResult['toolCallCount'] == (3 if call else 2)
    assert all(any(t['function']['name'] == 'add_notes' for t in schemas) for schemas in provider.tool_schemas)
    assert editor.state == before
    assert '第 1–1 小节' in assistant.executionSummary
    if call is None:
        assert assistant.lastTurnResult['status'] == 'needs_input'
        assert assistant.lastTurnResult['writeCount'] == 0
        assert len(provider.requests) == 2
        assert '等待补充素材' in assistant.executionSummary
        assert not assistant.proposal['available']
        assert 'do not invent a substitute' in provider.requests[0][0]['content']
    else:
        assert assistant.lastTurnResult['proposalStatus'] == 'pending'
        assert '提案待应用' in assistant.executionSummary
        assert assistant.auditionProposal()['success']
        assert preview.notes
        assert editor.state == before
        assert '已启动本地试听' in assistant.executionSummary
        assert assistant.applyProposal()['success']
        assert '已应用' in assistant.executionSummary
        assert '工具调用 3 次' in assistant.executionSummary
        assert editor.state.active_notes[0].pitch == (64 if scenario == 'original' else 62)
        assert '未保存修改' in assistant.executionSummary
        saved = tmp_path / 'result.pianoai.json'
        assert editor.saveProject(str(saved))
        _wait_until(qapp, lambda: not editor.isSaving)
        assert not editor.isDirty
        assert load_project(saved).active_notes == editor.state.active_notes
        assert '工程已保存' in assistant.executionSummary
    assistant.shutdown()
