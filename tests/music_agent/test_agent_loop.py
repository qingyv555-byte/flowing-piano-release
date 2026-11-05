from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.agent_loop import (
    MAX_TOOL_FEEDBACK_BYTES,
    MusicAgentLoop,
)
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.anthropic_provider import AnthropicProvider
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.proposal_manager import MusicEditProposalManager
from ai_piano_studio.music_agent.providers import (
    DeepSeekProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderError,
    ProviderEvent,
    ProviderToolCall,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentLimits,
    MusicExecutionMode,
    MusicPermission,
    MusicToolResult,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry


TOOLS = ModelCapabilities(True, False, False, False, True)
TEXT_ONLY = ModelCapabilities(False, False, False, False, True)


class _LocalProviderServer:
    """Loopback-only OpenAI-shaped server used to exercise the real adapter boundary."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []
        self.responses: dict[str, list[tuple[int, dict[str, str], bytes]]] = {}
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                parent._handle(self)

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> _LocalProviderServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        size = int(request.headers.get("Content-Length", "0"))
        body = request.rfile.read(size)
        self.requests.append((request.command, request.path, body))
        responses = self.responses.get(request.path, [])
        if responses:
            status, headers, response_body = responses.pop(0)
        else:
            status, headers, response_body = 404, {"Content-Type": "application/json"}, b'{"error":"missing"}'
        request.send_response(status)
        for name, value in headers.items():
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(response_body)))
        request.end_headers()
        request.wfile.write(response_body)


def _sse_event(delta: Mapping[str, object]) -> bytes:
    return b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"


def _sse_tool_call(call_id: str, name: str, arguments: Mapping[str, object]) -> bytes:
    return _sse_event(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        }
    ) + b"data: [DONE]\n\n"


def _local_provider(
    server: _LocalProviderServer,
    provider_type: type[OpenAICompatibleProvider],
    model_id: str,
    capabilities: ModelCapabilities,
) -> OpenAICompatibleProvider:
    credentials = MemoryCredentialStore()
    reference = credentials.put("local-test", "test-only-key")
    config = ProviderConfig(
        provider_id="local-test",
        base_url=server.base_url,
        credential_reference=reference,
        model_id=model_id,
        allow_local_http=True,
    )
    return provider_type(config, credentials, model_capabilities={model_id: capabilities})


def _real_loop(
    provider: OpenAICompatibleProvider, gateway: MusicAgentToolGateway, model_id: str
) -> MusicAgentLoop:
    return MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id=model_id,
    )


class _StaticContextBuilder:
    def build(self, instruction: str, _context: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {"role": "system", "content": "AI Piano Studio helper; use real Music Tools only."},
            {"role": "user", "content": instruction},
        ]


@dataclass
class _ScriptedProvider:
    scripts: list[tuple[ProviderEvent, ...] | BaseException]
    capabilities: ModelCapabilities = TOOLS

    def __post_init__(self) -> None:
        self.requests: list[tuple[list[Mapping[str, object]], list[Mapping[str, object]]]] = []

    def get_capabilities(self, _model_id: str) -> ModelCapabilities:
        return self.capabilities

    def stream_events(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        _reasoning: str | None,
        _attachments: Sequence[object],
    ) -> Iterator[ProviderEvent]:
        self.requests.append((list(messages), list(tools)))
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        yield from script

    def normalize_error(self, error: BaseException) -> ProviderError:
        return ProviderError("provider failed")


class _CapabilityFailureProvider(_ScriptedProvider):
    def get_capabilities(self, _model_id: str) -> ModelCapabilities:
        raise ProviderError("provider capability lookup failed")


def _editor() -> EditorController:
    return EditorController(
        ProjectState(notes=(Note.create(id="n2", pitch=64, start_time=2.0, duration=0.5, velocity=80),))
    )


def _context(gateway: MusicAgentToolGateway, permissions: set[MusicPermission], **kwargs: object):
    authorized_bars = kwargs.pop("authorized_bars", (BarRange(2, 2),))
    return gateway.create_execution_context(
        authorized_bars=authorized_bars, permissions=frozenset(permissions), **kwargs
    )


def _loop(provider: _ScriptedProvider, gateway: MusicAgentToolGateway, **kwargs: object) -> MusicAgentLoop:
    return MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=_StaticContextBuilder(),
        model_id="scripted",
        **kwargs,
    )


def test_native_claude_tool_turn_proposes_applies_and_undoes() -> None:
    def stream(events):
        return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)

    first = stream([
        {"type": "message_start"},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "native-1", "name": "transpose_notes", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"note_ids":["n2"],"semitones":2}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ])
    final = stream([
        {"type": "message_start"},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Staged."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ])
    with _LocalProviderServer() as server:
        server.responses["/v1/messages"] = [(200, {}, first), (200, {}, final)]
        editor = _editor()
        before = editor.state
        gateway = MusicAgentToolGateway(editor)
        provider = _local_provider(server, AnthropicProvider, "test-claude", TOOLS)
        outcome = _real_loop(provider, gateway, "test-claude").run(
            "Transpose the selected note.", _context(gateway, {MusicPermission.WRITE_NOTES})
        )
        assert outcome.status == "proposal"
        assert editor.state == before
        transcript = json.loads(server.requests[1][2])["messages"]
        assert transcript[-1]["content"][0]["tool_use_id"] == "native-1"
        assert transcript[-2]["content"][0]["name"] == "transpose_notes"
        manager = MusicEditProposalManager(editor, gateway)
        manager.register(outcome.context)
        assert manager.apply_proposal().success
        assert editor.state.active_notes[0].pitch == 66
        assert editor._history.undo_depth == 1
        editor.undo()
        assert editor.state == before


def test_local_openai_chat_turn_stays_text_only_without_a_proposal() -> None:
    """Break caught: an ordinary OpenAI-shaped reply stages an edit or mutates the project."""
    with _LocalProviderServer() as server:
        server.responses["/v1/chat/completions"] = [
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_event({"content": "和弦是同时响起的多个音。"}) + b"data: [DONE]\n\n",
            )
        ]
        editor = _editor()
        gateway = MusicAgentToolGateway(editor)
        before = editor.state

        outcome = _real_loop(
            _local_provider(server, OpenAIProvider, "gpt-4o-mini", TOOLS),
            gateway,
            "gpt-4o-mini",
        ).run("什么是和弦？", _context(gateway, set()))

    assert outcome.status == "finished"
    assert outcome.text == "和弦是同时响起的多个音。"
    assert outcome.proposal is None
    assert editor.state == before
    assert editor._history.undo_depth == 0
    assert [path for _method, path, _body in server.requests] == ["/v1/chat/completions"]


def test_local_openai_transpose_proposal_applies_once_and_undo_restores_notes() -> None:
    """Break caught: a real provider tool call cannot cross the Proposal Apply/Undo boundary."""
    with _LocalProviderServer() as server:
        server.responses["/v1/chat/completions"] = [
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_tool_call(
                    "transpose-1", "transpose_notes", {"note_ids": ["n2"], "semitones": 2}
                ),
            ),
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_event({"content": "已暂存修改。"}) + b"data: [DONE]\n\n",
            ),
        ]
        editor = _editor()
        gateway = MusicAgentToolGateway(editor)
        before = editor.state
        outcome = _real_loop(
            _local_provider(server, OpenAIProvider, "gpt-4o-mini", TOOLS),
            gateway,
            "gpt-4o-mini",
        ).run("将选中音符升高两个半音。", _context(gateway, {MusicPermission.WRITE_NOTES}))

    assert outcome.status == "proposal"
    assert outcome.proposal is not None
    assert [operation.name for operation in outcome.proposal.operations] == ["transpose_notes"]
    assert outcome.tool_call_count == 1
    assert outcome.read_count == 0
    assert outcome.write_count == 1
    assert editor.state == before

    manager = MusicEditProposalManager(editor, gateway)
    manager.register(outcome.context)

    assert manager.apply_proposal().success
    assert editor.state.active_notes[0].pitch == 66
    assert editor._history.undo_depth == 1
    editor.undo()
    assert editor.state == before


def test_local_deepseek_reads_compact_facts_then_stages_a_bounded_four_bar_continuation() -> None:
    """Break caught: a compatible read/write turn leaks raw state or stages notes beyond its four bars."""
    with _LocalProviderServer() as server:
        server.responses["/v1/chat/completions"] = [
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_tool_call("summary", "get_project_summary", {}),
            ),
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_tool_call(
                    "notes",
                    "get_notes",
                    {"start_bar": 2, "end_bar": 5},
                ),
            ),
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_tool_call(
                    "continue",
                    "add_notes",
                    {
                        "notes": [
                            {"pitch": 67, "start_time": 2.25, "duration": 0.5, "velocity": 80},
                            {"pitch": 69, "start_time": 4.25, "duration": 0.5, "velocity": 80},
                            {"pitch": 71, "start_time": 6.25, "duration": 0.5, "velocity": 80},
                            {"pitch": 72, "start_time": 8.25, "duration": 0.5, "velocity": 80},
                        ]
                    },
                ),
            ),
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_event({"content": "续写已暂存。"}) + b"data: [DONE]\n\n",
            ),
        ]
        editor = _editor()
        gateway = MusicAgentToolGateway(editor)
        before = editor.state
        outcome = _real_loop(
            _local_provider(server, DeepSeekProvider, "deepseek-chat", TOOLS),
            gateway,
            "deepseek-chat",
        ).run(
            "阅读当前摘要和音符，然后续写四小节。",
            _context(
                gateway,
                {MusicPermission.READ_PROJECT_SUMMARY, MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES},
                authorized_bars=(BarRange(2, 5),),
            ),
        )

    assert outcome.status == "proposal"
    assert outcome.proposal is not None
    continuation = outcome.proposal.operations[0].payload["notes"]
    assert len(continuation) == 4
    assert [note.start_time for note in continuation] == [2.25, 4.25, 6.25, 8.25]
    assert editor.state == before
    assert len(server.requests) == 4
    first_payload = json.loads(server.requests[0][2])
    assert {schema["function"]["name"] for schema in first_payload["tools"]} >= {
        "get_project_summary",
        "get_notes",
    }
    feedback_payload = json.loads(server.requests[2][2])
    feedback_messages = feedback_payload["messages"]
    feedback = [message["content"] for message in feedback_messages if message["role"] == "tool"]
    assert len(feedback) == 2
    assert '"bpm":120' in feedback[0]
    assert '"returned_note_count":1' in feedback[1]
    assert '"total_note_count":1' in feedback[1]
    assert '"id":"n2"' in feedback[1]
    assert '"pitch":64' in feedback[1]
    assert "ProjectState" not in "".join(feedback)
    pending_tool_calls: set[str] = set()
    for message in feedback_messages:
        if message["role"] == "assistant" and "tool_calls" in message:
            pending_tool_calls = {call["id"] for call in message["tool_calls"]}
            continue
        if message["role"] == "tool":
            assert message["tool_call_id"] in pending_tool_calls


def test_parallel_tool_results_keep_the_original_assistant_tool_turn_together() -> None:
    """Break caught: a strict provider rejects split assistant messages for one parallel tool turn."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("summary", "get_project_summary", "{}"),
                        ProviderToolCall(
                            "notes", "get_notes", '{"start_bar":2,"end_bar":2}'
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "write",
                            "add_notes",
                            '{"notes":[{"pitch":67,"start_time":2.25,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "Read the project and add one note.",
        _context(
            gateway,
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.WRITE_NOTES,
            },
        ),
    )

    assert outcome.status == "proposal"
    transcript = provider.requests[1][0][2:]
    assert [message["role"] for message in transcript] == ["assistant", "tool", "tool"]
    assert [call["id"] for call in transcript[0]["tool_calls"]] == ["summary", "notes"]
    assert [message["tool_call_id"] for message in transcript[1:]] == ["summary", "notes"]


def test_local_compatible_catalog_and_selected_tools_model_complete_a_tool_call() -> None:
    """Break caught: a discovered compatible model loses its declared tool protocol at turn time."""
    with _LocalProviderServer() as server:
        server.responses["/v1/models"] = [
            (200, {"Content-Type": "application/json"}, b'{"data":[{"id":"local-tools"}]}')
        ]
        server.responses["/v1/chat/completions"] = [
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_tool_call(
                    "transpose-local",
                    "transpose_notes",
                    {"note_ids": ["n2"], "semitones": 1},
                ),
            ),
            (
                200,
                {"Content-Type": "text/event-stream"},
                _sse_event({"content": "已暂存。"}) + b"data: [DONE]\n\n",
            ),
        ]
        editor = _editor()
        gateway = MusicAgentToolGateway(editor)
        provider = _local_provider(server, OpenAICompatibleProvider, "local-tools", TOOLS)

        assert provider.list_models() == ["local-tools"]
        outcome = _real_loop(provider, gateway, "local-tools").run(
            "升高选中音符。",
            _context(gateway, {MusicPermission.WRITE_NOTES}),
            intent="EDIT_MUSIC",
        )

    assert outcome.status == "proposal"
    assert outcome.proposal is not None
    payload = json.loads(server.requests[1][2])
    assert payload["model"] == "local-tools"
    assert any(schema["function"]["name"] == "transpose_notes" for schema in payload["tools"])


def test_scripted_transpose_stages_a_proposal_without_mutating_the_editor() -> None:
    """Break caught: a model write bypasses Proposal staging and changes the live editor."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "call-1",
                            "transpose_notes",
                            '{"note_ids":["n2"],"semitones":2}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="Staged.", finish_reason="stop"),),
        ]
    )
    before = editor.state

    outcome = _loop(provider, gateway).run(
        "Transpose the selected note.", _context(gateway, {MusicPermission.WRITE_NOTES})
    )

    assert outcome.status == "proposal"
    assert outcome.proposal is not None
    assert [operation.name for operation in outcome.proposal.operations] == ["transpose_notes"]
    assert editor.state == before


def test_tool_continuation_state_is_returned_only_to_provider() -> None:
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    before = editor.state
    provider = _ScriptedProvider([
        (ProviderEvent(text_delta="Staging.", reasoning_content="opaque-private-state", tool_calls=(
            ProviderToolCall("call-1", "transpose_notes", '{"note_ids":["n2"],"semitones":2}', "opaque-signature"),
        )),),
        (ProviderEvent(text_delta="Done.", finish_reason="stop"),),
    ])
    outcome = _loop(provider, gateway).run(
        "Transpose the selected note.", _context(gateway, {MusicPermission.WRITE_NOTES})
    )
    assert outcome.status == "proposal"
    assert editor.state == before
    assistant = next(message for message in provider.requests[1][0] if message.get("tool_calls"))
    assert assistant["reasoning_content"] == "opaque-private-state"
    assert assistant["content"] == "Staging."
    assert assistant["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "opaque-signature"}}
    assert "opaque-" not in repr(outcome)
    assert outcome.text == 'Done.'
    manager = MusicEditProposalManager(editor, gateway)
    manager.register(outcome.context)
    assert manager.apply_proposal().success
    editor.undo()
    assert editor.state == before


def test_chat_response_never_calls_gateway_or_creates_proposal() -> None:
    """Break caught: ordinary assistant text creates a write or an empty proposal."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([(ProviderEvent(text_delta="Try C major.", finish_reason="stop"),)])

    outcome = _loop(provider, gateway).run("What key suits this?", _context(gateway, set()))

    assert outcome.status == "finished"
    assert outcome.text == "Try C major."
    assert outcome.proposal is None
    assert gateway.current_proposal_calls == 0 if hasattr(gateway, "current_proposal_calls") else True


@pytest.mark.parametrize('text', ['请提供对应的简谱或五线谱，我可以据此暂存音符。', 'Please provide the score for the requested passage.'])
def test_missing_source_material_stops_without_forced_write_retries(text):
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider([(ProviderEvent(text_delta=text, finish_reason='stop'),)])
    before = editor.state
    outcome = _loop(provider, gateway).run('我要原曲', _context(gateway, {MusicPermission.WRITE_NOTES}), intent='CREATE_MUSIC')
    assert outcome.status == 'needs_input'
    assert outcome.text == text
    assert len(provider.requests) == 1
    assert outcome.write_count == 0 and outcome.proposal is None
    assert editor.state == before


def test_chat_turn_with_a_tool_capable_model_receives_no_music_schemas() -> None:
    """Break caught: a normal chat request exposes music tools merely because the model supports them."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [(ProviderEvent(text_delta="和弦是同时或依次出现的一组音。", finish_reason="stop"),)]
    )

    outcome = _loop(provider, gateway).run(
        "什么是和弦？", _context(gateway, set()), intent="CHAT"
    )

    assert outcome.status == "finished"
    assert provider.requests[0][1] == []


def test_collaboration_text_only_edit_is_not_reported_as_success() -> None:
    """A collaboration edit needs a real write, not a reassuring assistant sentence."""
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (ProviderEvent(text_delta="好的，我已经帮你修改好了。", finish_reason="stop"),),
        ]
    )
    context = _context(
        gateway,
        {
            MusicPermission.READ_PROJECT_SUMMARY,
            MusicPermission.READ_NOTES,
            MusicPermission.WRITE_NOTES,
        },
        mode=MusicExecutionMode.COLLABORATION,
        authorized_bars=(BarRange(1, 8),),
    )

    outcome = _loop(provider, gateway).run(
        "写一段4小节安静的旋律。", context, intent="CREATE_MUSIC"
    )

    assert outcome.status == "no_write"
    assert len(provider.requests) == 1  # A no-write finish does not coerce the model to try again.
    assert editor.state.active_notes == ()
    assert editor.projectRevision == context.baseline_revision
    assert editor._history.undo_depth == 0


def test_collaboration_create_commits_one_real_write_transaction() -> None:
    """A collaboration create must change the project and create one undo entry."""
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
        authorized_bars=(BarRange(1, 8),),
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "create",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.0,"duration":0.5,"velocity":72}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "写一段4小节安静的旋律。", context, intent="CREATE_MUSIC"
    )

    assert outcome.status == "finished"
    assert len(editor.state.active_notes) == 1
    assert editor._history.undo_depth == 1
    assert editor.projectRevision != context.baseline_revision


def test_collaboration_create_continues_after_bpm_write_until_notes_are_added() -> None:
    """A creation request must not finish merely because the model changed a setting first."""
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_BPM, MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
        authorized_bars=(BarRange(1, 8),),
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("tempo", "set_bpm", '{"bpm":96}'),)),),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "notes",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.0,"duration":0.5,"velocity":72}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run("创作一段旋律。", context, intent="CREATE_MUSIC")

    assert outcome.status == "finished"
    assert [result.tool_name for result in outcome.tool_results] == ["set_bpm", "add_notes"]
    assert outcome.tool_results[-1].added_notes == 1
    assert len(provider.requests) == 3
    assert len(editor.state.active_notes) == 1
    assert editor.state.tempo.bpm == 96


def test_collaboration_create_with_only_bpm_write_rolls_back_without_reporting_success() -> None:
    """A setting-only creation is not a composition and must leave the project unchanged."""
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_BPM, MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
        authorized_bars=(BarRange(1, 8),),
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("tempo", "set_bpm", '{"bpm":96}'),)),),
            (ProviderEvent(finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run("创作一段旋律。", context, intent="CREATE_MUSIC")

    assert outcome.status == "no_write"
    assert [result.tool_name for result in outcome.tool_results] == ["set_bpm"]
    assert len(provider.requests) == 2
    assert editor.state.active_notes == ()
    assert editor.state.tempo.bpm == 120
    assert editor._history.undo_depth == 0


def test_read_music_turn_exposes_only_read_only_registry_schemas() -> None:
    """Break caught: a music-reading request exposes write tools before the user asks to edit."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([(ProviderEvent(text_delta="已读取。", finish_reason="stop"),)])
    registry = MusicToolRegistry.default()

    outcome = _loop(provider, gateway).run(
        "分析这段音乐。", _context(gateway, set()), intent="READ_MUSIC"
    )

    assert outcome.status == "finished"
    assert provider.requests[0][1] == registry.openai_schemas(read_only=True)


def test_edit_turn_keeps_read_and_write_schemas_across_read_to_write_steps() -> None:
    """Break caught: a continuation loses write schemas after the first read result."""
    editor = _editor()
    before = editor.state
    gateway = MusicAgentToolGateway(editor)
    registry = MusicToolRegistry.default()
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("summary-1", "get_project_summary", "{}"),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "harmony-1",
                            "get_harmony_context",
                            '{"start_bar":2,"end_bar":2}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update-1",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":62}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "直接把当前选中的部分改得更梦幻，现在执行。",
        _context(
            gateway,
            {
                MusicPermission.READ_PROJECT_SUMMARY,
                MusicPermission.READ_NOTES,
                MusicPermission.READ_HARMONY,
                MusicPermission.WRITE_NOTES,
            },
        ),
        intent="EDIT_MUSIC",
    )

    assert outcome.status == "proposal"
    assert outcome.write_count == 1
    assert [result.tool_name for result in outcome.tool_results] == [
        "get_project_summary",
        "get_harmony_context",
        "update_notes",
    ]
    expected_tools = registry.openai_schemas()
    assert len(provider.requests) == 4
    assert all(request_tools == expected_tools for _messages, request_tools in provider.requests)
    assert outcome.debug_snapshot["intent"] == "EDIT_MUSIC"
    assert outcome.debug_snapshot["turnPermissions"] == (
        "read_harmony",
        "read_notes",
        "read_project_summary",
        "write_notes",
    )
    steps = outcome.debug_snapshot["steps"]
    assert [step["stepIndex"] for step in steps] == [1, 2, 3, 4]
    assert all(
        step["toolsSent"]
        == tuple(definition["function"]["name"] for definition in expected_tools)
        for step in steps
    )
    assert steps[-2]["toolResults"] == (
        {"tool": "update_notes", "success": True, "errorCode": None},
    )
    assert steps[-1]["toolResults"] == ()


def test_proposal_turn_continues_read_update_add_then_consumes_provider_final() -> None:
    """Break caught: the loop returns immediately after the first successful write."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("read", "get_notes", '{"start_bar":2,"end_bar":2}'),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":65}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "add",
                            "add_notes",
                            '{"notes":[{"pitch":67,"start_time":2.75,"duration":0.25,"velocity":84}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="两步修改均已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "读取后调整旋律并补一个音。",
        _context(
            gateway,
            {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES},
        ),
        intent="EDIT_MUSIC",
    )

    assert outcome.status == "proposal"
    assert outcome.text == "两步修改均已暂存。"
    assert [result.tool_name for result in outcome.tool_results] == [
        "get_notes",
        "update_notes",
        "add_notes",
    ]
    assert [operation.name for operation in outcome.proposal.operations] == [
        "update_notes",
        "add_notes",
    ]
    assert len(provider.requests) == 4
    assert editor.state.active_notes[0].pitch == 64


def test_collaboration_multi_write_commits_once_as_one_history_entry() -> None:
    """Break caught: each provider write becomes a separate commit or later writes never run."""
    editor = _editor()
    before = editor.state
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":65}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "add",
                            "add_notes",
                            '{"notes":[{"pitch":67,"start_time":2.75,"duration":0.25,"velocity":84}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成两步修改。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run("执行两步修改。", context, intent="EDIT_MUSIC")

    assert outcome.status == "finished"
    assert outcome.write_count == 2
    assert len(provider.requests) == 3
    assert editor.state.active_notes[0].pitch == 65
    assert len(editor.state.active_notes) == 2
    assert editor._history.undo_depth == 1
    editor.undo()
    assert editor.state == before


def test_proposal_read_after_write_observes_the_staged_note_state() -> None:
    """Break caught: a Proposal read after a write still sees the live editor baseline."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":69}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("reread", "get_notes", '{"start_bar":2,"end_bar":2}'),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已核对暂存结果。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "调整后再读取确认。",
        _context(
            gateway,
            {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES},
        ),
        intent="EDIT_MUSIC",
    )

    assert outcome.status == "proposal"
    reread_feedback = json.loads(str(provider.requests[2][0][-1]["content"]))
    assert reread_feedback["data"]["notes"][0]["id"] == "n2"
    assert reread_feedback["data"]["notes"][0]["pitch"] == 69
    assert editor.state.active_notes[0].pitch == 64


def test_provider_failure_after_first_collaboration_write_rolls_back_the_whole_turn() -> None:
    """Break caught: a provider failure after write one commits a partial collaboration edit."""
    editor = _editor()
    before = editor.state
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":65}]}',
                        ),
                    )
                ),
            ),
            ProviderError("provider failed after write"),
        ]
    )

    outcome = _loop(provider, gateway).run("执行两步修改。", context, intent="EDIT_MUSIC")

    assert outcome.status == "provider_error"
    assert editor.state == before
    assert editor._history.undo_depth == 0
    assert gateway.finish_turn(context).error_code == "conflict"


def test_provider_failure_after_proposal_write_discards_the_partial_workspace() -> None:
    """Break caught: a failed multi-step Proposal remains retrievable as a partial suggestion."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(gateway, {MusicPermission.WRITE_NOTES})
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":65}]}',
                        ),
                    )
                ),
            ),
            ProviderError("provider failed after proposal write"),
        ]
    )

    outcome = _loop(provider, gateway).run("生成两步建议。", context, intent="EDIT_MUSIC")

    assert outcome.status == "provider_error"
    assert editor.state.active_notes[0].pitch == 64
    with pytest.raises(RuntimeError, match="released"):
        gateway.current_proposal(context)


def test_cancellation_after_first_collaboration_write_rolls_back_the_whole_turn() -> None:
    """Break caught: cancellation between writes leaves the first collaboration write applied."""
    editor = _editor()
    before = editor.state
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "update",
                            "update_notes",
                            '{"updates":[{"id":"n2","pitch":65}]}',
                        ),
                    )
                ),
            )
        ]
    )
    checks = iter((False, False, False, True))

    outcome = _loop(
        provider, gateway, is_cancelled=lambda: next(checks, True)
    ).run("执行两步修改。", context, intent="EDIT_MUSIC")

    assert outcome.status == "cancelled"
    assert editor.state == before
    assert editor._history.undo_depth == 0
    assert gateway.finish_turn(context).error_code == "conflict"


def test_text_deltas_are_forwarded_before_the_chat_turn_finishes() -> None:
    """Break caught: a streamed reply is withheld until the whole Agent Loop completes."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(text_delta="正在读取工程。"),
                ProviderEvent(text_delta="已准备好。", finish_reason="stop"),
            )
        ]
    )
    forwarded: list[str] = []

    outcome = _loop(provider, gateway, on_text_delta=forwarded.append).run(
        "给我一条进度。", _context(gateway, set())
    )

    assert forwarded == ["正在读取工程。", "已准备好。"]
    assert outcome.text == "正在读取工程。已准备好。"


@pytest.mark.parametrize("name", ["read_file", "shell", "python", "delete_project"])
def test_unknown_tool_never_uses_python_name_lookup(name: str) -> None:
    """Break caught: an unregistered provider name can reach Python or the editor."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("call-1", name, "{}"),)),),
            (ProviderEvent(text_delta="I can only use Music Tools.", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run("Do it.", _context(gateway, set()))

    assert outcome.status == "unknown_tool"
    assert outcome.tool_results[0].error_code == "unknown_tool"
    assert outcome.tool_results[0].tool_name == "unregistered"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("raw", ["", "{", "null", "[]", "1"])
def test_invalid_json_arguments_return_a_safe_failure(raw: str) -> None:
    """Break caught: malformed or scalar tool JSON reaches the Gateway validation path."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("call-1", "get_notes", raw),)),),
            (ProviderEvent(text_delta="Please retry.", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "Read bar 2.", _context(gateway, {MusicPermission.READ_NOTES})
    )

    assert outcome.tool_results[0].error_code == "validation_error"
    assert outcome.tool_results[0].data == {}


def test_duplicate_id_and_unauthorized_bar_stop_without_a_gateway_write() -> None:
    """Break caught: replayed IDs or unauthorized ranges can produce a staged edit."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                        tool_calls=(
                            ProviderToolCall("duplicate", "get_notes", '{"start_bar":2,"end_bar":2}'),
                            ProviderToolCall("duplicate", "transpose_notes", '{"note_ids":["n2"],"semitones":1}'),
                    )
                ),
            ),
            (ProviderEvent(text_delta="Stopped.", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "Change it.",
        _context(gateway, {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}),
        intent="EDIT_MUSIC",
    )

    assert outcome.status == "validation_error"
    assert [result.error_code for result in outcome.tool_results] == [None, "validation_error"]
    assert outcome.proposal is None
    assert len(provider.requests) == 2


def test_twelve_model_requests_do_not_consume_gateway_tool_budget() -> None:
    """Break caught: provider requests double-count against the Gateway's per-tool budget."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall(f"call-{index}", "get_project_summary", "{}"),)),)
            for index in range(12)
        ]
    )
    context = _context(
        gateway,
        {MusicPermission.READ_PROJECT_SUMMARY},
        limits=MusicAgentLimits(max_steps=12),
    )

    outcome = _loop(provider, gateway).run("Keep trying.", context, intent="READ_MUSIC")

    assert outcome.status == "limit_exceeded"
    assert len(provider.requests) == 12
    assert context.budget.steps == 12


def test_repeated_identical_validation_failure_stops_recovery() -> None:
    """Identical invalid arguments stop recovery even when the model generates a fresh call ID."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall(f"invalid-{index}", "add_notes", "{}"),)),)
            for index in range(3)
        ]
    )

    outcome = _loop(provider, gateway).run(
        "写一段旋律。",
        _context(gateway, {MusicPermission.WRITE_NOTES}),
        intent="CREATE_MUSIC",
    )

    assert outcome.status == "validation_error"
    assert [result.error_code for result in outcome.tool_results] == [
        "validation_error",
        "validation_error",
    ]
    assert len(provider.requests) == 2


def test_distinct_validation_failures_can_be_corrected_more_than_once() -> None:
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([
        (ProviderEvent(tool_calls=(ProviderToolCall('missing', 'get_notes', '{}'),)),),
        (ProviderEvent(tool_calls=(ProviderToolCall('bad-page', 'get_notes',
            '{"start_bar":2,"end_bar":2,"offset":-1}'),)),),
        (ProviderEvent(tool_calls=(ProviderToolCall('valid', 'get_notes',
            '{"start_bar":2,"end_bar":2,"offset":0}'),)),),
        (ProviderEvent(text_delta='Read successfully.', finish_reason='stop'),),
    ])
    outcome = _loop(provider, gateway).run(
        'Read bar 2.', _context(gateway, {MusicPermission.READ_NOTES}), intent='READ_MUSIC')
    assert [result.error_code for result in outcome.tool_results] == [
        'validation_error', 'validation_error', None]
    assert len(provider.requests) == 4


def test_non_validation_tool_error_does_not_start_a_model_correction_request() -> None:
    """Break caught: permissions failures invite an unauthorized model correction loop."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("denied", "transpose_notes", '{"note_ids":["n2"],"semitones":1}'),)),),
            (ProviderEvent(text_delta="This must not be requested.", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "升高音符。",
        _context(gateway, set()),
        intent="EDIT_MUSIC",
    )

    assert outcome.status == "permission_denied"
    assert len(provider.requests) == 1


def test_create_turn_allows_more_than_four_successful_reads_before_a_write() -> None:
    """A model may inspect the current music before committing to a concrete edit."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall(f"summary-{index}", "get_project_summary", "{}"),)),)
            for index in range(6)
        ]
        + [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "write-1",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":2.5,"duration":0.25,"velocity":70}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "续写四小节。",
        _context(gateway, {MusicPermission.READ_PROJECT_SUMMARY, MusicPermission.WRITE_NOTES}),
        intent="CREATE_MUSIC",
    )

    assert outcome.status == "proposal"
    assert len(provider.requests) == 8
    assert [result.success for result in outcome.tool_results] == [True] * 7


def test_ninth_write_and_2001_note_attempt_stop_at_gateway_limits() -> None:
    """Break caught: loop-side handling bypasses write or note caps before staging."""
    gateway = MusicAgentToolGateway(_editor())
    ninth = _ScriptedProvider(
        [(ProviderEvent(tool_calls=(ProviderToolCall("w9", "set_bpm", '{"bpm":121}'),)),)]
    )
    ninth_context = _context(
        gateway, {MusicPermission.WRITE_BPM}, limits=MusicAgentLimits(max_write_calls=8)
    )
    ninth_context.budget.write_calls = 8

    ninth_outcome = _loop(ninth, gateway).run("Set BPM.", ninth_context)

    oversized = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "big",
                            "add_notes",
                            '{"notes":[{"pitch":72,"start_time":2.5,"duration":0.25,"velocity":80}]}',
                        ),
                    )
                ),
            )
        ]
    )
    oversized_context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        limits=MusicAgentLimits(max_added_notes=2_000),
    )
    oversized_context.budget.added_notes = 2_000
    oversized_outcome = _loop(oversized, gateway).run("Add notes.", oversized_context)

    assert ninth_outcome.status == "limit_exceeded"
    assert ninth_outcome.tool_results[0].error_code == "limit_exceeded"
    assert oversized_outcome.status == "limit_exceeded"
    assert oversized_outcome.tool_results[0].error_code == "limit_exceeded"


def test_text_only_model_receives_no_schemas_and_cannot_start_tool_turn() -> None:
    """Break caught: conservative/text-only capability metadata still enables model tool execution."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [(ProviderEvent(tool_calls=(ProviderToolCall("call-1", "transpose_notes", '{"note_ids":["n2"],"semitones":1}'),)),)],
        capabilities=TEXT_ONLY,
    )

    outcome = _loop(provider, gateway).run(
        "Transpose it.", _context(gateway, {MusicPermission.WRITE_NOTES})
    )

    assert provider.requests[0][1] == []
    assert outcome.status == "tools_unavailable"
    assert outcome.proposal is None


def test_tools_model_receives_only_registry_schemas() -> None:
    """Break caught: provider receives arbitrary schemas rather than the registered Music Tools."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([(ProviderEvent(text_delta="Hello.", finish_reason="stop"),)])
    registry = MusicToolRegistry.default()

    _loop(provider, gateway).run("分析这段音乐。", _context(gateway, set()), intent="READ_MUSIC")

    assert provider.requests[0][1] == registry.openai_schemas(read_only=True)


def test_model_without_verified_streaming_capability_stops_before_a_provider_request() -> None:
    """Break caught: an ID-only catalog model reaches the provider and becomes a fake network error."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([], capabilities=ModelCapabilities(False, False, False, False, False))

    outcome = _loop(provider, gateway).run(
        "写一段10秒的月光。", _context(gateway, {MusicPermission.WRITE_NOTES})
    )

    assert outcome.status == "model_unavailable"
    assert provider.requests == []


def test_provider_error_while_reading_capabilities_ends_compactly() -> None:
    """Break caught: a non-recoverable provider capability error escapes the bounded loop."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _CapabilityFailureProvider([])

    outcome = _loop(provider, gateway).run("Hello.", _context(gateway, set()))

    assert outcome.status == "provider_error"
    assert outcome.text == ""
    assert provider.requests == []


def test_provider_recovery_category_survives_the_agent_loop() -> None:
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([ProviderError("rate limited", code="rate_limit")])

    outcome = _loop(provider, gateway).run("继续四小节", _context(gateway, set()))

    assert outcome.status == "rate_limit"


def test_unexpected_provider_exception_is_not_written_raw_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Break caught: provider diagnostics containing credentials are emitted through logger.exception."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider([RuntimeError("bearer provider-secret-123")])

    outcome = _loop(provider, gateway).run("Hello.", _context(gateway, set()))

    assert outcome.status == "provider_error"
    assert "provider-secret-123" not in caplog.text


def test_collaboration_write_finishes_the_existing_turn_without_a_proposal() -> None:
    """Break caught: a collaboration preview is returned as a Proposal and left uncommitted."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "write",
                            "transpose_notes",
                            '{"note_ids":["n2"],"semitones":2}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="Finished.", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run("Transpose it.", context)

    assert outcome.status == "finished"
    assert outcome.proposal is None
    assert editor.state.active_notes[0].pitch == 66
    assert editor._history.undo_depth == 1
    assert gateway.finish_turn(context).error_code == "conflict"


def test_cancelled_collaboration_turn_is_rolled_back() -> None:
    """Break caught: cancellation leaves a begun collaboration transaction active or previewed."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    before = editor.state
    provider = _ScriptedProvider([])

    outcome = _loop(provider, gateway, is_cancelled=lambda: True).run("Stop.", context)

    assert outcome.status == "cancelled"
    assert editor.state == before
    assert gateway.finish_turn(context).error_code == "conflict"


def test_provider_error_rolls_back_a_begun_collaboration_turn() -> None:
    """Break caught: provider failure leaks the active collaboration transaction."""
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.WRITE_NOTES},
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _ScriptedProvider([ProviderError("safe failure")])

    outcome = _loop(provider, gateway).run("Stop.", context)

    assert outcome.status == "provider_error"
    assert gateway.finish_turn(context).error_code == "conflict"


def test_tool_feedback_projects_raw_data_to_a_bounded_safe_summary() -> None:
    """Break caught: provider feedback forwards arbitrary raw Gateway data without a byte bound."""
    result = MusicToolResult.success_result(
        "get_notes",
        "x" * 10_000,
        data={
            "start_bar": 1,
            "end_bar": 8,
            "notes": [
                {
                    "id": f"safe-note-{index}",
                    "pitch": 60 + index % 12,
                    "start_time": index * 0.125,
                    "duration": 0.125,
                    "velocity": 80,
                    "secret": "x" * 10_000,
                }
                for index in range(1_000)
            ],
            "path": "C:/secret",
        },
    )

    feedback = MusicAgentLoop._tool_feedback_messages([("call", result)])[0]
    encoded = str(feedback["content"]).encode("utf-8")
    payload = json.loads(str(feedback["content"]))

    assert len(encoded) <= MAX_TOOL_FEEDBACK_BYTES
    assert payload["data"]["truncated"] is True
    assert payload["data"]["total_note_count"] == 1_000
    assert 0 < payload["data"]["returned_note_count"] < 1_000
    assert payload["data"]["notes"][0] == {
        "id": "safe-note-0",
        "pitch": 60,
        "start_time": 0.0,
        "duration": 0.125,
        "velocity": 80,
    }
    assert "C:/secret" not in str(feedback["content"])
    assert "secret" not in str(feedback["content"])


def test_dense_66_note_read_returns_exact_authorized_records_to_the_next_provider_request() -> None:
    """Break caught: a dense authorized read reaches the provider as note_count only."""
    notes = tuple(
        Note.create(
            id=f"dense-{index:02d}-{'x' * 80}",
            pitch=48 + index % 24,
            start_time=index * 0.125,
            duration=0.1,
            velocity=55 + index % 45,
        )
        for index in range(66)
    )
    editor = EditorController(ProjectState(notes=notes))
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "read-66", "get_notes", '{"start_bar":1,"end_bar":5}'
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已读取真实音符。", finish_reason="stop"),),
        ]
    )
    loop = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="scripted",
    )
    context = _context(
        gateway,
        {MusicPermission.READ_NOTES},
        authorized_bars=(BarRange(1, 5),),
    )

    outcome = loop.run(
        "分析当前 1–5 小节的旋律。",
        context,
        requested_bars=(BarRange(1, 5),),
        intent="READ_MUSIC",
    )

    assert outcome.status == "finished"
    assert len(provider.requests) == 2
    feedback = json.loads(str(provider.requests[1][0][-1]["content"]))
    assert feedback["data"]["returned_note_count"] == 66
    assert feedback["data"]["total_note_count"] == 66
    assert feedback["data"]["truncated"] is False
    assert feedback["data"]["notes"][0] == {
        "id": notes[0].id,
        "pitch": 48,
        "start_time": 0.0,
        "duration": 0.1,
        "velocity": 55,
    }
    assert feedback["data"]["notes"][-1]["id"] == notes[-1].id


def test_get_notes_provider_feedback_contains_authorized_notes_but_not_adjacent_bars() -> None:
    """Break caught: actionable note feedback leaks note IDs outside the authorized read range."""
    allowed = Note.create(id="allowed-note", pitch=60, start_time=0.5, duration=0.25, velocity=70)
    forbidden = Note.create(id="forbidden-note", pitch=72, start_time=2.5, duration=0.25, velocity=99)
    editor = EditorController(ProjectState(notes=(allowed, forbidden)))
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "read-authorized", "get_notes", '{"start_bar":1,"end_bar":1}'
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="完成。", finish_reason="stop"),),
        ]
    )

    outcome = _loop(provider, gateway).run(
        "读取第一小节。",
        _context(
            gateway,
            {MusicPermission.READ_NOTES},
            authorized_bars=(BarRange(1, 1),),
        ),
        intent="READ_MUSIC",
    )

    assert outcome.status == "finished"
    feedback = str(provider.requests[1][0][-1]["content"])
    assert "allowed-note" in feedback
    assert "forbidden-note" not in feedback
    assert '"pitch":60' in feedback


def test_harmony_feedback_keeps_provider_only_token_and_actionable_candidates() -> None:
    """Break caught: Harmony feedback loses the token and candidates needed by apply_chord."""
    result = MusicToolResult.success_result(
        "get_harmony_context",
        "harmony ready",
        data={
            "start_bar": 1,
            "end_bar": 2,
            "harmony_context_token": "provider-token-123",
            "measures": [
                {
                    "measure_index": 0,
                    "temporary": False,
                    "candidates": [
                        {
                            "candidate_index": 0,
                            "chord_symbol": "Cm",
                            "quality": "minor",
                            "score": 0.875,
                            "private": "never-forward",
                        }
                    ],
                }
            ],
            "path": "C:/private-project",
        },
    )

    feedback = MusicAgentLoop._tool_feedback_messages([("harmony", result)])[0]
    payload = json.loads(str(feedback["content"]))

    assert payload["data"]["harmony_context_token"] == "provider-token-123"
    assert payload["data"]["measures"] == [
        {
            "measure_index": 0,
            "candidates": [
                {
                    "candidate_index": 0,
                    "chord_symbol": "Cm",
                    "quality": "minor",
                    "score": 0.875,
                }
            ],
        }
    ]
    assert "private" not in str(feedback["content"])
    assert "C:/private-project" not in str(feedback["content"])


def test_apply_chord_accepts_the_token_and_candidate_returned_in_provider_feedback() -> None:
    """Break caught: the provider-visible Harmony reference cannot drive the registered write tool."""
    melody = (
        (72, 0.0, 0.5),
        (83, 0.5, 0.25),
        (76, 0.75, 0.25),
        (79, 1.0, 0.5),
        (77, 1.5, 0.25),
        (81, 1.75, 0.125),
        (72, 1.875, 0.125),
    )
    editor = EditorController(
        ProjectState(
            notes=tuple(
                Note.create(
                    id=f"melody-{index}",
                    pitch=pitch,
                    start_time=start_time,
                    duration=duration,
                    velocity=80,
                )
                for index, (pitch, start_time, duration) in enumerate(melody)
            )
        )
    )
    gateway = MusicAgentToolGateway(editor)
    context = _context(
        gateway,
        {MusicPermission.READ_HARMONY, MusicPermission.WRITE_HARMONY},
        authorized_bars=(BarRange(1, 1),),
    )
    harmony = gateway.execute(
        "get_harmony_context", {"start_bar": 1, "end_bar": 1}, context
    )
    feedback = json.loads(MusicAgentLoop._tool_feedback_content(harmony))["data"]
    candidate = feedback["measures"][0]["candidates"][0]

    applied = gateway.execute(
        "apply_chord",
        {
            "measure_index": feedback["measures"][0]["measure_index"],
            "candidate_index": candidate["candidate_index"],
            "harmony_context_token": feedback["harmony_context_token"],
        },
        context,
    )

    assert applied.success
    assert [operation.name for operation in gateway.current_proposal(context).operations] == [
        "apply_chord"
    ]


def test_cancellation_between_events_prevents_later_gateway_calls_and_feedback_is_compact() -> None:
    """Break caught: cancellation allows a later tool call or leaks internal state in tool feedback."""
    gateway = MusicAgentToolGateway(_editor())
    provider = _ScriptedProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("read", "get_notes", '{"start_bar":2,"end_bar":2}'),)),),
            (ProviderEvent(tool_calls=(ProviderToolCall("write", "transpose_notes", '{"note_ids":["n2"],"semitones":1}'),)),),
        ]
    )
    checks = iter((False, False, False, False, True))

    outcome = _loop(provider, gateway, is_cancelled=lambda: next(checks, True)).run(
        "Read then transpose.", _context(gateway, {MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES})
    )

    assert outcome.status == "cancelled"
    assert len(outcome.tool_results) == 1
    assert provider.requests[1][0][-1]["role"] == "tool"
    feedback = str(provider.requests[1][0][-1]["content"])
    assert '"tool":"get_notes"' in feedback
    assert "ProjectState" not in feedback
    assert "session:" not in feedback


@pytest.mark.parametrize('reply', ['想先确认，你指的是哪首同名歌曲？', '你更想先调整旋律，还是伴奏？', '你希望是什么风格？', '请先告诉我这段音乐的用途。'])
def test_material_clarification_is_not_forced_into_an_arbitrary_write(reply):
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    provider = _ScriptedProvider([(ProviderEvent(text_delta=reply, finish_reason='stop'),)])
    before = editor.state
    outcome = _loop(provider, gateway).run('调整一下这首曲子',
        _context(gateway, {MusicPermission.WRITE_NOTES}), intent='EDIT_MUSIC')
    assert outcome.status == 'needs_input' and outcome.text == reply
    assert len(provider.requests) == 1 and outcome.write_count == 0
    assert editor.state == before


def test_unavailable_following_score_is_not_forced_into_a_guessed_continuation():
    editor = _editor()
    gateway = MusicAgentToolGateway(editor)
    reply = "未找到可核对的后续曲谱，暂不写入，保留现有开头。本次原曲续写尚未完成。"
    provider = _ScriptedProvider([(ProviderEvent(text_delta=reply, finish_reason="stop"),)])
    before = editor.state
    outcome = _loop(provider, gateway).run("按青花瓷原曲往下接到25秒",
        _context(gateway, {MusicPermission.WRITE_NOTES}), intent="EDIT_MUSIC")
    assert outcome.status == "needs_input"
    assert len(provider.requests) == 1 and outcome.write_count == 0
    assert editor.state == before
