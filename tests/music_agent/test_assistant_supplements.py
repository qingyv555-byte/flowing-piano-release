"""Input during real source/model work must be delivered without widening authority."""
import json
from threading import Event

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoopOutcome
from ai_piano_studio.music_agent.assistant_controller import AssistantMessageKind
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.tool_contracts import MusicExecutionMode
from tests.music_agent.test_assistant_controller import (
    _SerializedLoopProvider, _serialized_loop_assistant, _wait_until,
)
from tests.music_agent.test_creation_workflow import assistant_for, done


class BlockingProvider(_SerializedLoopProvider):
    def __init__(self, scripts):
        super().__init__(scripts)
        self.entered, self.release = Event(), Event()

    def stream_events(self, messages, tools, reasoning, attachments):
        self.requests.append(list(messages))
        self.tool_schemas.append(list(tools))
        if len(self.requests) == 1:
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError('test did not release the owned worker')
        if self._scripts:
            yield from self._scripts.pop(0)
        else:
            yield ProviderEvent(text_delta='', finish_reason='stop')


def write(name, arguments):
    return (ProviderEvent(tool_calls=(ProviderToolCall(name, name, json.dumps(arguments)),)),)


def test_busy_messages_steer_the_next_request_and_discard_stale_writes(tmp_path, qapp):
    note = Note.create(id='source', pitch=60, start_time=0, duration=1, velocity=80)
    editor = EditorController(ProjectState(notes=(note,)))
    provider = BlockingProvider([
        write('transpose_notes', {'note_ids': ['source'], 'semitones': 12}),
        write('set_note_velocity', {'note_ids': ['source'], 'velocity': 55}),
    ])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assert assistant.sendMessage('把当前旋律升高八度')
    _wait_until(qapp, provider.entered.is_set)
    generation, task_id = assistant._active_generation, assistant.taskProgress['taskId']
    try:
        assert assistant.sendMessage('不要升高八度，保持原音高')
        assert assistant.sendMessage('力度改为55')
        assert assistant.pendingMessageCount == 2 and assistant.isBusy
        assert assistant._active_generation == generation
        assert editor.state.active_notes == (note,)
        assistant._generationTextDelta.emit(generation, '旧请求的迟到回复不应显示')
        qapp.processEvents()
        assert not any('迟到回复' in m.text for m in assistant.messages.entries)
    finally:
        provider.release.set()
    done(qapp, assistant)
    assert assistant.pendingMessageCount == 0
    assert assistant.taskProgress['taskId'] == task_id
    assert assistant.proposal['status'] == 'pending'
    assert editor.state.active_notes == (note,)
    assert '不要升高八度' in json.dumps(provider.requests[1], ensure_ascii=False)
    assert '力度改为55' in json.dumps(provider.requests[1], ensure_ascii=False)
    users = [m.text for m in assistant.messages.entries if m.kind == AssistantMessageKind.USER_MESSAGE]
    assert users == ['把当前旋律升高八度', '不要升高八度，保持原音高', '力度改为55']
    assert assistant.applyProposal()['success']
    assert editor.state.active_notes[0].pitch == 60  # Superseded octave write never ran.
    assert editor.state.active_notes[0].velocity == 55


def test_stop_discards_pending_input_and_late_worker_cannot_write(tmp_path, qapp):
    note = Note.create(id='source', pitch=60, start_time=0, duration=1)
    editor = EditorController(ProjectState(notes=(note,)))
    provider = BlockingProvider([write('transpose_notes', {'note_ids': ['source'], 'semitones': 12})])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assert assistant.sendMessage('把当前旋律升高八度')
    _wait_until(qapp, provider.entered.is_set)
    try:
        assert assistant.sendMessage('再柔和一点')
        assistant.cancelGeneration()
        assert assistant.pendingMessageCount == 0 and not assistant.isBusy
    finally:
        provider.release.set()
    _wait_until(qapp, lambda: not assistant._workers)
    assert editor.state.active_notes == (note,)
    assert not assistant.proposal['available']
    assert len(provider.requests) == 1


def test_supplement_cannot_expand_the_active_edit_scope(tmp_path, qapp):
    inside = Note.create(id='inside', pitch=60, start_time=0, duration=.5)
    outside = Note.create(id='outside', pitch=67, start_time=8, duration=.5)
    editor = EditorController(ProjectState(notes=(inside, outside)))
    provider = BlockingProvider([
        write('transpose_notes', {'note_ids': ['inside'], 'semitones': 12}),
        write('transpose_notes', {'note_ids': ['outside'], 'semitones': 12}),
    ])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assert assistant.sendMessage('把第1小节的旋律升高八度')
    _wait_until(qapp, provider.entered.is_set)
    original_scope = assistant._active_context.authorized_bars
    try:
        assert assistant.sendMessage('把第5小节也升高八度')
        assert assistant._active_context.authorized_bars == original_scope
    finally:
        provider.release.set()
    done(qapp, assistant)
    assert editor.state.active_notes == (inside, outside)
    assert not assistant.proposal['available']


def test_source_lookup_supplement_rebuilds_the_preview_with_latest_duration(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor)
    workflow = assistant._creation_workflow
    original = workflow.resolver.resolve
    entered, release = Event(), Event()
    calls = []
    def lookup(instruction, **kwargs):
        calls.append(instruction)
        if len(calls) == 1:
            entered.set()
            if not release.wait(5):
                raise RuntimeError('source test did not release')
        return original(instruction, **kwargs)
    workflow.resolver.resolve = lookup
    assert assistant.sendMessage('弹一段月光奏鸣曲，10s')
    _wait_until(qapp, entered.is_set)
    try:
        assert assistant.sendMessage('改为15s，温柔一点')
        assert assistant.pendingMessageCount == 1
    finally:
        release.set()
    done(qapp, assistant)
    assert len(calls) == 2 and '15s' in calls[1]
    assert assistant.taskPreviewAvailable
    assert 14 <= assistant.taskPreviewInfo['durationSeconds'] <= 15.01
    assert assistant.pendingMessageCount == 0 and not provider.requests
    assert not playback.notes and not editor.state.notes
    assert not assistant.applyTaskProject()


def test_input_arriving_between_worker_completion_and_ui_delivery_is_not_lost(tmp_path, qapp):
    editor = EditorController(ProjectState())
    provider = _SerializedLoopProvider([(ProviderEvent(text_delta='按补充要求回答一句话。', finish_reason='stop'),)])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    context = assistant.gateway.create_execution_context(authorized_bars=(), permissions=frozenset(), mode=MusicExecutionMode.PROPOSAL)
    assistant._generation = assistant._active_generation = 7
    assistant._active_cancel = Event()
    assistant._active_context = context
    assistant._active_intent = 'CHAT'
    assistant._assistant_status = 'thinking'
    assistant._retry_instruction = '介绍钢琴'
    assistant._task_progress.start(7, '介绍钢琴', 'compatible', 'scripted-model')
    assert assistant.sendMessage('只说一句话')
    assistant._on_generation_finished(7, MusicAgentLoopOutcome('finished', '旧回复', context), None)
    done(qapp, assistant)
    assert assistant.messages.entries[-1].text == '按补充要求回答一句话。'
    assert '只说一句话' in json.dumps(provider.requests[0], ensure_ascii=False)
    assert not provider.tool_schemas[0] and assistant.pendingMessageCount == 0
