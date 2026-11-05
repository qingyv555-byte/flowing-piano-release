"""Studio opts into the real native session while gateway validation stays external."""
import json
import pytest
from threading import Event

from ai_piano_studio.music_agent import codex_native, codex_provider
from ai_piano_studio.music_agent.codex_provider import CodexProvider
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall


def native_supplement_fixture(monkeypatch, pending, *, between_rounds=False):
    """A native continuation fails if its superseded pending call is reused."""
    sessions = []
    monkeypatch.setattr(codex_provider, 'find_codex', lambda: 'fixture-codex')
    monkeypatch.setattr(codex_provider, 'run_codex', lambda *a, **k: 'Logged in using ChatGPT')
    class Session:
        complete = search_observed = False
        public_search_actions = ()
        def __init__(self, *a, **k):
            self.requests, self.closed = [], False
            self.diagnostics = {'process_starts':1,'turn_starts':1}
            sessions.append(self)
        def stream_events(self, messages, tools, reasoning, attachments=()):
            self.requests.append([dict(m) for m in messages])
            if len(self.requests) > 1:
                if self is sessions[0]:
                    from ai_piano_studio.music_agent.providers import ProviderError
                    raise ProviderError('stale native continuation', code='native_tool_mismatch')
                self.complete = True
                yield ProviderEvent(text_delta='按补充处理完成。',finish_reason='stop')
            elif self is sessions[0]:
                if not between_rounds:
                    pending.append('不要移调，力度改为55')
                name = 'get_notes' if between_rounds else 'transpose_notes'
                args = {'start_bar':2,'end_bar':2} if between_rounds else {'note_ids':['n2'],'semitones':12}
                yield ProviderEvent(tool_calls=(ProviderToolCall('old',name,json.dumps(args)),))
            else:
                assert any('力度改为55' in str(m.get('content')) for m in messages)
                yield ProviderEvent(tool_calls=(ProviderToolCall('new','set_note_velocity',
                    json.dumps({'note_ids':['n2'],'velocity':55})),))
        def close(self): self.closed = True
    monkeypatch.setattr(codex_native, 'CodexNativeSession', Session)
    provider = CodexProvider('scripted')
    provider.native_tools_enabled = True
    return provider, sessions


@pytest.mark.parametrize('between_rounds', [False,True])
def test_native_supplement_restarts_pending_turn_and_keeps_gateway_scope(monkeypatch, between_rounds):
    from tests.music_agent.test_agent_loop import _editor, _context, _loop
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission
    pending = []
    provider, sessions = native_supplement_fixture(monkeypatch,pending,between_rounds=between_rounds)
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    invalidations = []
    gateway.invalidate_score_image_delivery = lambda: invalidations.append(True)
    if between_rounds:
        original = gateway.execute
        def execute(*a,**k):
            result = original(*a,**k)
            if not pending and len(sessions)==1: pending.append('力度改为55')
            return result
        monkeypatch.setattr(gateway,'execute',execute)
    def take_pending():
        result = list(pending)
        pending.clear()
        return result
    context = _context(gateway,{MusicPermission.READ_NOTES,MusicPermission.WRITE_NOTES})
    outcome = _loop(provider,gateway,take_pending_messages=take_pending).run('调整当前旋律',context=context,intent='EDIT_MUSIC')
    assert outcome.context is context
    assert outcome.status == 'proposal'
    assert len(sessions)==2 and all(s.closed for s in sessions)
    assert invalidations
    assert editor.state.active_notes[0].pitch == 64
    assert outcome.proposal is not None
    assert provider.native_diagnostics['process_starts']==2
    assert context.budget.write_calls == 1
    assert context.budget.steps == (2 if between_rounds else 1)


@pytest.mark.parametrize('completed', [False,True])
def test_native_reset_invalidates_old_image_claims_and_accumulates_diagnostics(monkeypatch, completed):
    pending = []
    provider,sessions = native_supplement_fixture(monkeypatch,pending)
    messages = [{'role':'user','content':'读取乐谱'}]
    list(provider.stream_events(messages,[],None,()))
    prior = sessions[0]
    prior.public_search_actions = [{'type':'search','query':'public score'}]
    if completed:
        prior.complete=True
        provider.close()
        assert provider.native_context_reset_required
    assert provider.reset_for_supplement()
    assert prior.closed
    old_feedback = json.dumps({'data':{'status':'visual_score','images_delivered':True,
        'source_sha256':'hash','next_action':'Use previously delivered images'}})
    messages += [{'role':'tool','tool_call_id':'prior-read','content':old_feedback},
                 {'role':'user','content':'力度改为55'}]
    try:
        list(provider.stream_events(messages,[],None,()))
    finally:
        provider.close()
    delivered = sessions[1].requests[0]
    feedback = next(json.loads(m['content']) for m in delivered if m.get('role')=='tool')
    assert feedback['data']['images_delivered'] is False
    assert 'cached images' in feedback['data']['next_action']
    assert json.loads(old_feedback)['data']['images_delivered'] is True
    assert any('unavailable in this new session' in m.get('content','') for m in delivered if m.get('role')=='developer')
    assert provider.native_diagnostics['process_starts']==2
    assert provider.native_diagnostics['turn_starts']==2
    assert provider.native_search_actions==[{'type':'search','query':'public score'}]


def test_no_write_completion_does_not_spawn_unrequested_native_retries(monkeypatch):
    # Capability policy: model completion is no longer followed by synthetic
    # "write now" user messages that launch extra paid sessions.
    from tests.music_agent.test_agent_loop import _editor, _context, _loop
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission
    provider,sessions = native_supplement_fixture(monkeypatch,[])
    session_type = codex_native.CodexNativeSession
    def finish_without_notes(self,*args,**kwargs):
        self.complete=True
        yield ProviderEvent(text_delta='继续准备。',finish_reason='stop')
    monkeypatch.setattr(session_type,'stream_events',finish_without_notes)
    gateway = MusicAgentToolGateway(_editor())
    invalidations=[]
    gateway.invalidate_score_image_delivery=lambda:invalidations.append(True)
    context = _context(gateway,{MusicPermission.READ_NOTES,MusicPermission.WRITE_NOTES})
    outcome = _loop(provider,gateway).run('创作一段旋律',context=context,intent='CREATE_MUSIC')
    assert outcome.status=='no_write'
    assert len(sessions)==1 and all(s.closed for s in sessions)
    assert not invalidations
    assert provider.native_diagnostics['process_starts']==1
    assert outcome.context is context


def test_late_cancellation_cannot_restart_native_or_execute_superseded_write(monkeypatch):
    from tests.music_agent.test_agent_loop import _editor, _context, _loop
    from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission
    cancelled=Event()
    pending=[]
    provider,sessions=native_supplement_fixture(monkeypatch,pending)
    original=codex_native.CodexNativeSession.stream_events
    def cancel_before_delivering(self,*args,**kwargs):
        for event in original(self,*args,**kwargs):
            cancelled.set()
            yield event
    monkeypatch.setattr(codex_native.CodexNativeSession,'stream_events',cancel_before_delivering)
    provider.is_cancelled=cancelled.is_set
    editor=_editor()
    gateway=MusicAgentToolGateway(editor)
    context=_context(gateway,{MusicPermission.READ_NOTES,MusicPermission.WRITE_NOTES})
    outcome=_loop(provider,gateway,is_cancelled=cancelled.is_set,
                  take_pending_messages=lambda:list(pending)).run('调整旋律',context=context,intent='EDIT_MUSIC')
    assert outcome.status=='cancelled'
    assert len(sessions)==1 and sessions[0].closed
    assert context.budget.write_calls==0 and editor.state.active_notes[0].pitch==64


def test_studio_constructs_native_transport_for_normal_conversation(tmp_path):
    from tests.music_agent.test_codex_controller import assistant_at
    from ai_piano_studio.music_agent.creation_workflow import CreationWorkflow
    assistant = assistant_at(tmp_path)
    assistant.selectProvider("codex")
    assistant.saveProviderSettings("codex", "", "", "", "test-cli-model", False)
    assistant._creation_workflow = CreationWorkflow(assistant, None)
    try:
        loop = assistant._create_agent_loop(1, Event())
        assert loop._provider.native_tools_enabled
        assert not loop._gateway._resource_session.eager_score_reference
        assistant._creation_workflow.reconstructing = True
        reconstruction = assistant._create_agent_loop(2, Event())
        assert reconstruction._gateway._resource_session.eager_score_reference
    finally:
        assistant.shutdown()


def test_gateway_rounds_reuse_one_native_session_and_never_exec_json(monkeypatch):
    sessions, commands = [], []
    monkeypatch.setattr(codex_provider, "find_codex", lambda: "codex.exe")
    def metadata(path, args, **kwargs):
        commands.append(args)
        assert args == ["login", "status"]
        return "Logged in using ChatGPT"
    monkeypatch.setattr(codex_provider, "run_codex", metadata)
    class Session:
        complete = False
        search_observed = True
        diagnostics = {"process_starts": 1, "turn_starts": 1}
        def __init__(self, *args, **kwargs):
            self.calls, self.closed = [], False
            sessions.append(self)
        def stream_events(self, messages, tools, reasoning, attachments=()):
            self.calls.append(messages)
            if len(self.calls) == 1:
                yield ProviderEvent(tool_calls=(ProviderToolCall("native-1", "get_notes", "{}"),))
            else:
                assert messages[-1]["tool_call_id"] == "native-1"
                self.complete = True
                yield ProviderEvent(text_delta="已完成这次读取。", finish_reason="stop")
        def close(self):
            self.closed = True
    monkeypatch.setattr(codex_native, "CodexNativeSession", Session)
    provider = CodexProvider("same-model")
    provider.native_tools_enabled = True
    tools = [{"type": "function", "function": {"name": "get_notes", "parameters": {"type": "object"}}}]
    messages = [{"role": "user", "content": "看看这段"}]
    assert list(provider.stream_events(messages, tools, None, ()))[0].tool_calls[0].call_id == "native-1"
    messages += [{"role": "tool", "tool_call_id": "native-1", "content": "{}"}]
    assert list(provider.stream_events(messages, tools, None, ()))[0].text_delta
    assert len(sessions) == 1 and len(sessions[0].calls) == 2 and sessions[0].closed
    assert commands == [["login", "status"]]
    assert provider.native_diagnostics["turn_starts"] == 1


def test_native_song_does_not_run_a_separate_source_preflight(tmp_path, qapp, monkeypatch):
    from tests.music_agent.test_creation_workflow import assistant_for
    from ai_piano_studio.editor.controller import EditorController
    from ai_piano_studio.domain.project import ProjectState
    assistant, _, _ = assistant_for(tmp_path, EditorController(ProjectState()))
    workflow = assistant._creation_workflow
    monkeypatch.setattr(workflow.resolver, "resolve", lambda *a, **k: None)
    class Provider:
        native_tools_enabled = True
        def search_music_sources(self, *a, **k):
            raise AssertionError("The single native turn must discover and prepare its own source")
    try:
        assert workflow._resolve(1, Event(), "来一首周杰伦的烟花易冷", Provider()) is None
    finally:
        assistant.shutdown()


def test_native_child_honors_existing_system_proxy_without_global_changes(monkeypatch):
    import os
    import urllib.request
    for name in tuple(os.environ):
        if name.lower() in {"https_proxy", "http_proxy", "no_proxy"}:
            monkeypatch.delenv(name)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {
        "https": "http://127.0.0.1:8781", "http": "http://127.0.0.1:8781", "no": "localhost,127.0.0.1"})
    child = codex_provider._cli_env()
    assert child["HTTPS_PROXY"] == "http://127.0.0.1:8781"
    assert child["HTTP_PROXY"] == "http://127.0.0.1:8781"
    assert child["NO_PROXY"] == "localhost,127.0.0.1"
    assert "HTTPS_PROXY" not in os.environ


def test_explicit_child_proxy_preference_is_preserved(monkeypatch):
    import urllib.request
    monkeypatch.setenv("HTTPS_PROXY", "http://explicit.example.org:8080")
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:8781"})
    assert codex_provider._cli_env()["HTTPS_PROXY"] == "http://explicit.example.org:8080"
