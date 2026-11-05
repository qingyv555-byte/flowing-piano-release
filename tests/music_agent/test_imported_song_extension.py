"""Real MIDI -> adopt -> edit/extend -> save/load/undo, including imported layers."""
import json
from dataclasses import replace

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.domain.tempo import TempoMap
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.assistant_controller import AssistantMessageKind
from ai_piano_studio.music_agent.song_resolver import ResolvedMusic, validate_midi
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicExecutionMode, MusicPermission
from ai_piano_studio.music_agent.tool_gateway import MAX_COMPACT_CONTEXT_NOTES, MusicAgentToolGateway
from ai_piano_studio.persistence.midi import export_midi, materialize_midi_import
from ai_piano_studio.persistence.project_document import load_project_document
from ai_piano_studio.persistence.project_library import ProjectLibrary
from tests.music_agent.test_assistant_controller import _wait_until
from tests.music_agent.test_creation_workflow import assistant_for, done, accept_prepared


def imported_source(tmp_path):
    notes = tuple(Note.create(pitch=60 + i % 5 if track == 1 else 48,
        start_time=i, duration=1, velocity=70, track=track, channel=track - 1)
        for i in range(30) for track in (1, 2))
    path = tmp_path / 'two-track-reference.mid'
    export_midi(path, notes, TempoMap(bpm=60))
    return ResolvedMusic('双声部练习', validate_midi(path.read_bytes()),
                         {'kind': 'local_user_reference', 'verifiedNotes': True})


def adopted_assistant(tmp_path, qapp):
    source = imported_source(tmp_path)
    editor = EditorController(ProjectState(), project_library=ProjectLibrary(tmp_path / 'Projects'))
    assistant, playback, provider = assistant_for(tmp_path, editor)
    assistant._creation_workflow.resolver.resolve = lambda *args, **kwargs: source
    assert assistant.sendMessage('弹一段《双声部练习》，10s')
    done(qapp, assistant)
    accept_prepared(assistant)
    assert editor.state.playback_duration == pytest.approx(10)
    assert len(editor.state.layers) == 2
    return editor, assistant, provider, source


@pytest.mark.parametrize('count', [2, MAX_COMPACT_CONTEXT_NOTES + 2])
def test_compact_harmony_keeps_imported_layers_in_normal_and_capped_paths(tmp_path, qapp, count):
    source = imported_source(tmp_path)
    notes, layers = materialize_midi_import(source.midi, TempoMap(bpm=60))
    notes = tuple(replace(notes[i % len(notes)], id=f'n{i}', start_time=0) for i in range(count))
    editor = EditorController(ProjectState(notes=notes, tempo=TempoMap(bpm=60), layers=layers))
    before = editor.state
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_PROJECT_SUMMARY, MusicPermission.READ_NOTES, MusicPermission.READ_HARMONY}))
    messages = MusicAgentContextBuilder(gateway).build('分析当前声部与和声', context, intent='READ_MUSIC')
    assert messages and all(layer.id in messages[-1]['content'] for layer in layers)
    assert editor.state == before


@pytest.mark.parametrize('instruction', ['还不错，扩展到25s，直接修改进去', '不要修改开头，扩展到25s，直接修改进去'])
def test_adopted_multitrack_song_extends_10_to_25_directly_then_save_load_and_undo(tmp_path, qapp, instruction):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    before = editor.state
    undo_depth = editor._history.undo_depth
    continuation = [dict(pitch=64, start_time=i, duration=1, velocity=70,
                         layer_id=editor.state.layers[0].id) for i in range(10, 25)]
    provider._scripts.extend([
        (ProviderEvent(tool_calls=(ProviderToolCall('read', 'get_notes', '{"start_bar":1,"end_bar":3}'),)),),
        (ProviderEvent(tool_calls=(ProviderToolCall('extend', 'add_notes', json.dumps({'notes': continuation})),)),),
    ])
    assert assistant.sendMessage(instruction)
    done(qapp, assistant)
    assert provider.requests, 'The real worker must reach the provider after context construction'
    prompt = '\n'.join(m['content'] for m in provider.requests[0])
    assert '接着写后续乐句' in prompt and '不是复制开头' in prompt
    assert 'duration alone is not such permission' in prompt
    assert 'Do not ask the user to fetch, import or upload' in prompt
    assert 'This app has no song-search tool' not in prompt
    assert 'stop and ask for it' not in prompt
    assert assistant.assistantStatus == 'idle', [m.text for m in assistant.messages.entries]
    editor.synchronizeNow()
    assert editor.state.playback_duration == pytest.approx(25)
    assert all(replace(actual, updated_at=expected.updated_at) == expected
               for actual, expected in zip(editor.state.active_notes[:len(before.notes)], before.notes, strict=True))
    assert editor.state.layers == before.layers
    assert not assistant.proposal['available'] and assistant.mode == 'suggestion'
    assert editor._history.undo_depth == undo_depth + 1
    assert assistant.lastTurnResult['committed']
    editor.saveCurrent()
    _wait_until(qapp, lambda: not editor.isSaving)
    assert load_project_document(editor.currentFilePath).state.content == editor.state.content
    editor.undo()
    editor.synchronizeNow()
    assert editor.state.content == before.content


@pytest.mark.parametrize('text,expected', [
    ('扩展到25s，直接修改进去', MusicExecutionMode.COLLABORATION),
    ('延长到25秒，直接写入工程', MusicExecutionMode.COLLABORATION),
    ('扩展到25s，先试听', MusicExecutionMode.PROPOSAL),
    ('扩展到25s，不要直接应用', MusicExecutionMode.PROPOSAL),
    ('能直接修改进去吗？先聊聊', MusicExecutionMode.PROPOSAL),
])
def test_direct_application_is_per_turn_and_respects_preview_or_negation(tmp_path, qapp, text, expected):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    assert assistant._execution_mode_for_instruction(text) is expected
    assert assistant.mode == 'suggestion'


def test_extension_without_direct_request_stays_a_proposal(tmp_path, qapp):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    before = editor.state.content
    provider._scripts.append((ProviderEvent(tool_calls=(ProviderToolCall('add', 'add_notes',
        '{"notes":[{"pitch":64,"start_time":24,"duration":1,"velocity":70}]}'),)),))
    assert assistant.sendMessage('把这段扩展到25s，先试听')
    done(qapp, assistant)
    assert editor.state.content == before
    assert assistant.proposal['status'] == 'pending'
    assert assistant.applyProposal()['success']
    editor.synchronizeNow()
    assert editor.state.playback_duration == pytest.approx(25)


def test_explicit_manual_range_is_not_widened_by_a_duration_request(tmp_path, qapp):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    assistant.setCreationScopeBars(1, 2)
    context = assistant._create_execution_context('EDIT_MUSIC', '扩展到25s，直接修改进去')
    assert context.authorized_bars == (BarRange(1, 2),)
    assert MusicPermission.WHOLE_PROJECT_WRITE not in context.permissions


def test_manual_range_overrides_a_previous_followup_scope(tmp_path, qapp):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    note_id = editor.state.active_notes[0].id
    provider._scripts.append((ProviderEvent(tool_calls=(ProviderToolCall('soft', 'set_note_velocity',
        json.dumps({'note_ids': [note_id], 'velocity': 60})),)),))
    assert assistant.sendMessage('把当前旋律力度调为60')
    done(qapp, assistant)
    assert assistant.proposal['available']
    assistant.setCreationScopeBars(1, 2)
    context = assistant._create_execution_context('EDIT_MUSIC', '继续改')
    assert context.authorized_bars == (BarRange(1, 2),)


@pytest.mark.parametrize('ending', ['直接修改进去', '先试听'])
def test_short_extension_is_not_applied_or_offered_as_complete(tmp_path, qapp, ending):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    before = editor.state.content
    provider._scripts.append((ProviderEvent(tool_calls=(ProviderToolCall('short', 'add_notes',
        '{"notes":[{"pitch":64,"start_time":10,"duration":1,"velocity":70}]}'),)),))
    assert assistant.sendMessage('扩展到25s，' + ending)
    done(qapp, assistant)
    assert editor.state.content == before
    assert not assistant.proposal['available']
    assert assistant.taskProgress['safeErrorCode'] == 'incomplete_music'
    assert not assistant.lastTurnResult['committed']


def test_context_failure_reports_pre_request_error_without_fake_ai_advice(tmp_path, qapp, monkeypatch, caplog):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    before = editor.state.content
    def broken(*args, **kwargs):
        raise ValueError('private-reference-must-not-be-logged')
    monkeypatch.setattr(MusicAgentContextBuilder, 'build', broken)
    start = len(assistant.messages.entries)
    assert assistant.sendMessage('将当前旋律的力度调为55')
    done(qapp, assistant)
    assert not provider.requests and editor.state.content == before
    assert assistant.taskProgress['safeErrorCode'] == 'context_error'
    replies = assistant.messages.entries[start + 1:]
    assert len(replies) == 1 and replies[0].kind == AssistantMessageKind.ERROR
    assert '尚未发送给模型' in replies[0].text
    assert 'private-reference-must-not-be-logged' not in caplog.text
    assert 'ValueError' in caplog.text and 'broken' in caplog.text


def test_codex_native_transport_can_extend_an_adopted_multilayer_song(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent.codex_provider import CodexProvider
    from ai_piano_studio.music_agent import codex_provider, codex_native
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    notes = [dict(pitch=67, start_time=i, duration=1, velocity=70,
                  layer_id=editor.state.layers[0].id) for i in range(10, 25)]
    # Studio now uses a continuous native turn. Keep the same imported-MIDI,
    # bounded extension and real controller assertions, replacing only the old
    # JSON/exec transport fixture; subprocess protocol behavior has its own tests.
    sessions = []
    monkeypatch.setattr(codex_provider, 'find_codex', lambda: 'metadata-only-codex')
    def metadata(path, args, **kwargs):
        assert args == ['login', 'status']
        return 'Logged in using ChatGPT'
    monkeypatch.setattr(codex_provider, 'run_codex', metadata)
    class Session:
        complete = False
        search_observed = False
        diagnostics = {'process_starts': 1, 'turn_starts': 1}
        def __init__(self, *args, **kwargs):
            self.calls, self.closed = [], False
            self.web_search = kwargs['web_search']
            sessions.append(self)
        def stream_events(self, messages, tools, reasoning, attachments=()):
            self.calls.append(messages)
            if len(self.calls) == 1:
                yield ProviderEvent(text_delta='正在补上后面的15秒。', tool_calls=(
                    ProviderToolCall('native-add', 'add_notes', json.dumps({'notes': notes})),))
            else:
                assert messages[-1]['role'] == 'tool' and messages[-1]['tool_call_id'] == 'native-add'
                self.complete = True
                yield ProviderEvent(text_delta='音符已经写入当前工程。', finish_reason='stop')
        def close(self): self.closed = True
    monkeypatch.setattr(codex_native, 'CodexNativeSession', Session)
    native = CodexProvider('scripted-model')
    assistant._provider_settings._provider_factory = lambda _provider_id: native
    assert assistant.sendMessage('还不错，扩展到25s，直接修改进去')
    done(qapp, assistant)
    editor.synchronizeNow()
    assert editor.state.playback_duration == pytest.approx(25)
    assert assistant.lastTurnResult['committed'] and not assistant.proposal['available']
    assert len(sessions) == 1 and sessions[0].web_search and sessions[0].closed
    assert len(sessions[0].calls) == 2
