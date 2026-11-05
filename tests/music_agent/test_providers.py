from __future__ import annotations

import json
import io
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import pytest

from ai_piano_studio.music_agent import ModelProviderRegistry as PublicProviderRegistry
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.providers import (
    MAX_RESPONSE_BYTES,
    MAX_STREAM_LINE_BYTES,
    DeepSeekProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderError,
)
from ai_piano_studio.music_agent.registry import ModelProviderRegistry
from ai_piano_studio.music_agent.anthropic_provider import AnthropicProvider


def _claude_stream(*, tool=True, stop="tool_use"):
    block = {"type": "tool_use", "id": "call-1", "name": "music_capability_probe", "input": {}} if tool else {"type": "text", "text": ""}
    delta = {"type": "input_json_delta", "partial_json": "{}"} if tool else {"type": "text_delta", "text": "Hello"}
    events = [
        {"type": "message_start", "message": {}},
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop}},
        {"type": "message_stop"},
    ]
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


def test_claude_native_probe_headers_models_and_tool_transcript(provider_server):
    config, credentials = _config(provider_server, "anthropic", "test-claude")
    provider = AnthropicProvider(config, credentials, model_capabilities={"test-claude": ModelCapabilities(True, False, False, False, True)})
    provider_server.responses["/v1/models?limit=100"] = (200, {}, b'{"data":[{"id":"a"}],"has_more":true,"last_id":"a"}')
    provider_server.responses["/v1/models?limit=100&after_id=a"] = (200, {}, b'{"data":[{"id":"b"}],"has_more":false}')
    assert provider.list_models() == ["a", "b"]
    provider_server.responses["/v1/messages"] = (200, {}, _claude_stream())
    assert provider.verify_tool_capability().supports_tools
    headers = {key.lower(): value for key, value in provider_server.requests[-1][2].items()}
    assert headers["x-api-key"] == "fake-provider-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers
    request = json.loads(provider_server.requests[-1][3])
    assert request["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert request["max_tokens"] > 0
    provider_server.responses["/v1/messages"] = (200, {}, _claude_stream(tool=False, stop="end_turn"))
    messages = [
        {"role": "system", "content": "Music only"},
        {"role": "user", "content": "Read notes"},
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_notes", "arguments": "{}"}}, {"id": "b", "function": {"name": "read_notes", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "first"},
        {"role": "tool", "tool_call_id": "b", "content": "second"},
    ]
    assert "".join(provider.stream_chat(messages, [], None, [])) == "Hello"
    request = json.loads(provider_server.requests[-1][3])
    assert request["system"] == "Music only"
    assert request["messages"][-1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "first"},
        {"type": "tool_result", "tool_use_id": "b", "content": "second"},
    ]}


@pytest.mark.parametrize("body", [
    _claude_stream(stop="max_tokens"),
    _claude_stream().replace(b'data: {"type": "content_block_stop", "index": 0}\n\n', b""),
    _claude_stream().replace(b'data: {"type": "message_stop"}\n\n', b""),
    b'data: {"type":"error","error":{"message":"private-server-data"}}\n\n',
])
def test_claude_incomplete_or_error_stream_never_yields_tools(provider_server, body):
    config, credentials = _config(provider_server, "anthropic", "test")
    response = io.BytesIO(body)
    observed = []
    with pytest.raises(ProviderError) as raised:
        for event in AnthropicProvider(config, credentials)._stream_events(response):
            observed.extend(event.tool_calls)
    assert not observed
    assert response.closed
    assert "private-server-data" not in str(raised.value)


def test_compatible_stream_keeps_private_continuation_state(provider_server):
    chunks = [
        {"reasoning_content": "opaque-"},
        {"reasoning_content": "continuation", "content": "Visible"},
        {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "read_notes", "arguments": "{"}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
        {"tool_calls": [{"index": 0, "extra_content": {"google": {"thought_signature": "opaque-signature"}}}]},
    ]
    body = b"".join(b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n" for delta in chunks) + b"data: [DONE]\n\n"
    events = list(_provider(provider_server)._stream_events(io.BytesIO(body)))
    assert "".join(event.text_delta for event in events) == "Visible"
    assert events[-1].reasoning_content == "opaque-continuation"
    assert events[-1].tool_calls[0].thought_signature == "opaque-signature"
    assert events[-1].tool_calls[0].arguments_json == "{}"
    assert "opaque-" not in repr(events)


class _MockProviderServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.responses: dict[
            str, tuple[int, dict[str, str], bytes] | list[tuple[int, dict[str, str], bytes]]
        ] = {}
        self.delays: dict[str, float] = {}
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

    def __enter__(self) -> _MockProviderServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        size = int(request.headers.get("Content-Length", "0"))
        body = request.rfile.read(size)
        self.requests.append((request.command, request.path, dict(request.headers), body))
        delay = self.delays.get(request.path)
        if delay is not None:
            time.sleep(delay)
        configured_response = self.responses.get(
            request.path,
            (404, {"Content-Type": "application/json"}, b'{"error":"missing"}'),
        )
        if isinstance(configured_response, list):
            status, headers, response_body = configured_response.pop(0)
        else:
            status, headers, response_body = configured_response
        request.send_response(status)
        for name, value in headers.items():
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(response_body)))
        request.end_headers()
        request.wfile.write(response_body)


@pytest.fixture
def provider_server() -> Iterator[_MockProviderServer]:
    with _MockProviderServer() as server:
        yield server


def _config(server: _MockProviderServer, provider_id: str = "compatible", model_id: str = "") -> tuple[ProviderConfig, MemoryCredentialStore]:
    credentials = MemoryCredentialStore()
    reference = credentials.put(provider_id, "fake-provider-key")
    return (
        ProviderConfig(
            provider_id=provider_id,
            base_url=server.base_url,
            credential_reference=reference,
            model_id=model_id,
            allow_local_http=True,
        ),
        credentials,
    )


def _provider(server: _MockProviderServer, *, timeout_seconds: float = 2.0) -> OpenAICompatibleProvider:
    config, credentials = _config(server)
    return OpenAICompatibleProvider(config, credentials, timeout_seconds=timeout_seconds)


def test_list_models_returns_ids_from_a_local_openai_compatible_server(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: model-list payloads silently produce an empty model picker."""
    provider_server.responses["/v1/models"] = (
        200,
        {"Content-Type": "application/json"},
        json.dumps({"object": "list", "data": [{"id": "piano-1"}, {"id": "piano-2"}]}).encode(),
    )

    assert _provider(provider_server).list_models() == ["piano-1", "piano-2"]


def test_connection_uses_the_local_model_endpoint(provider_server: _MockProviderServer) -> None:
    """Break caught: a connection check reports success without contacting the provider."""
    provider_server.responses["/v1/models"] = (
        200, {"Content-Type": "application/json"}, b'{"data": []}'
    )

    assert _provider(provider_server).test_connection() is True
    assert provider_server.requests[0][2]["Authorization"] == "Bearer fake-provider-key"


@pytest.mark.parametrize("status", [401, 429])
def test_http_auth_and_rate_limit_errors_are_scrubbed(
    provider_server: _MockProviderServer, status: int
) -> None:
    """Break caught: provider errors expose credential-shaped response text."""
    provider_server.responses["/v1/models"] = (
        status,
        {"Content-Type": "application/json"},
        b'{"error":"Authorization: Bearer fake-provider-key"}',
    )

    with pytest.raises(ProviderError) as raised:
        _provider(provider_server).list_models()

    assert str(status) in str(raised.value)
    assert "fake-provider-key" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(401, "authentication"), (403, "authentication"), (404, "unsupported_capability"),
     (413, "context_too_large"), (429, "rate_limit"), (503, "provider_unavailable")],
)
def test_http_provider_failures_are_classified_for_recovery(
    provider_server: _MockProviderServer, status: int, expected_code: str
) -> None:
    provider_server.responses["/v1/models"] = (
        status,
        {"Content-Type": "application/json"},
        b'{"error":"safe failure"}',
    )

    with pytest.raises(ProviderError) as raised:
        _provider(provider_server).list_models()

    assert raised.value.code == expected_code


def test_bare_credential_in_provider_error_body_is_scrubbed(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a provider can echo an unlabelled bearer value into consumer errors."""
    provider_server.responses["/v1/models"] = (
        401,
        {"Content-Type": "application/json"},
        b'{"error":"fake-provider-key"}',
    )

    with pytest.raises(ProviderError) as raised:
        _provider(provider_server).list_models()

    assert "fake-provider-key" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


def test_transport_exception_cannot_leak_credential_through_its_cause(
    provider_server: _MockProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: exception chaining retains an external error that includes the bearer value."""
    provider = _provider(provider_server)

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("transport failed for fake-provider-key")

    monkeypatch.setattr(provider._opener, "open", fail_open)

    with pytest.raises(ProviderError) as raised:
        provider.list_models()

    assert raised.value.__cause__ is None
    assert "fake-provider-key" not in "".join(
        traceback.format_exception(raised.value)
    )


def test_timeout_becomes_a_scrubbed_provider_error(provider_server: _MockProviderServer) -> None:
    """Break caught: an unresponsive provider blocks indefinitely or leaks transport detail."""
    provider_server.delays["/v1/models"] = 0.2

    with pytest.raises(ProviderError, match="timed out"):
        _provider(provider_server, timeout_seconds=0.01).list_models()


def test_model_listing_rejects_a_response_larger_than_the_cap(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: an oversized response is accepted into process memory."""
    provider_server.responses["/v1/models"] = (
        200,
        {"Content-Type": "application/json"},
        b"x" * (MAX_RESPONSE_BYTES + 1),
    )

    with pytest.raises(ProviderError, match="too large"):
        _provider(provider_server).list_models()


def test_openai_reasoning_option_is_mapped_to_the_provider_payload(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a supported reasoning choice is omitted or sent under the wrong key."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"content":"ready"}}]}\n\ndata: [DONE]\n\n',
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="o3")

    result = list(OpenAIProvider(config, credentials).stream_chat(
        [{"role": "user", "content": "make a piano loop"}], [], "low", []
    ))

    assert result == ["ready"]
    payload = json.loads(provider_server.requests[0][3])
    assert payload["reasoning_effort"] == "low"
    assert payload["stream"] is True


def test_unsupported_reasoning_option_is_a_clear_scrubbed_error(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: unsupported reasoning reaches an incompatible provider request."""
    config, credentials = _config(provider_server, provider_id="openai", model_id="o3")

    with pytest.raises(ProviderError, match="unsupported reasoning option") as raised:
        list(OpenAIProvider(config, credentials).stream_chat([], [], "secret-option", []))

    assert "secret-option" not in str(raised.value)


def test_attachments_are_rejected_even_when_model_metadata_allows_files(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: pre-Phase-8 attachments disappear from a request without user feedback."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b"data: [DONE]\n\n",
    )
    config, credentials = _config(provider_server, model_id="file-capable")
    file_capable = ModelCapabilities(False, False, False, True, True)
    provider = OpenAICompatibleProvider(
        config, credentials, model_capabilities={"file-capable": file_capable}
    )

    with pytest.raises(ProviderError, match="attachments are not available"):
        list(provider.stream_chat([], [], None, [object()]))

    assert provider_server.requests == []


def test_stream_chat_yields_content_chunks_from_a_bounded_sse_response(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: streaming returns raw provider framing instead of consumer content chunks."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"content":"first"}}]}\n\ndata: {"choices":[{"delta":{"content":" second"}}]}\n\ndata: [DONE]\n\n',
    )
    config, credentials = _config(provider_server, model_id="known")
    provider = OpenAICompatibleProvider(
        config,
        credentials,
        model_capabilities={"known": OpenAIProvider.CAPABILITIES["o3"]},
    )

    assert list(provider.stream_chat([{"role": "user", "content": "hello"}], [], "low", [])) == [
        "first",
        " second",
    ]


def test_stream_events_finishes_on_finish_reason_without_done_sentinel(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a complete compatible stream hangs when the vendor omits [DONE]."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"content":"ready"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    )
    config, credentials = _config(
        provider_server, provider_id="openai", model_id="gpt-4o-mini"
    )

    events = list(
        OpenAIProvider(config, credentials).stream_events([], [], None, ())
    )

    assert [event.text_delta for event in events[:-1]] == ["ready"]
    assert events[-1].finish_reason == "stop"
    assert events[-1].tool_calls == ()


def test_stream_events_finishes_tool_call_without_done_sentinel(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a complete tool call hangs before the Agent can execute it."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"get_notes","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\n',
    )
    config, credentials = _config(
        provider_server, provider_id="openai", model_id="gpt-4o-mini"
    )

    events = list(
        OpenAIProvider(config, credentials).stream_events(
            [], [{"type": "function"}], None, ()
        )
    )

    assert events[-1].finish_reason == "tool_calls"
    assert events[-1].tool_calls[0].call_id == "call-1"
    assert events[-1].tool_calls[0].name == "get_notes"
    assert events[-1].tool_calls[0].arguments_json == "{}"


def test_stream_events_normalizes_a_fragmented_tool_call(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: vendor tool-call chunks leak into the Agent layer unassembled."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"content":"I will ","tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"transpose_","arguments":"{\\"note_ids\\":[\\"n1\\"]"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"do it.","tool_calls":[{"index":0,"function":{"name":"notes","arguments":",\\"semitones\\":12}"}}]}}]}\n\n'
        b"data: [DONE]\n\n",
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    events = list(OpenAIProvider(config, credentials).stream_events(
        [{"role": "user", "content": "transpose this"}], [{"type": "function"}], None, ()
    ))

    assert [event.text_delta for event in events[:-1]] == ["I will ", "do it."]
    assert events[-1].tool_calls[0].call_id == "call-1"
    assert events[-1].tool_calls[0].name == "transpose_notes"
    assert events[-1].tool_calls[0].arguments_json == '{"note_ids":["n1"],"semitones":12}'


def test_stream_events_retries_one_transient_server_failure(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a transient provider failure ends a valid streaming turn prematurely."""
    provider_server.responses["/v1/chat/completions"] = [
        (503, {"Content-Type": "application/json"}, b'{"error":"unavailable"}'),
        (200, {"Content-Type": "text/event-stream"}, b"data: [DONE]\n\n"),
    ]
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    events = list(OpenAIProvider(config, credentials).stream_events([], [], None, ()))

    assert events[-1].tool_calls == ()
    assert len(provider_server.requests) == 2


@pytest.mark.parametrize("arguments_json", ["{", "", "null"])
def test_stream_events_preserves_unvalidated_tool_arguments(
    provider_server: _MockProviderServer, arguments_json: str
) -> None:
    """Break caught: provider normalization parses or repairs arguments before Gateway validation."""
    event = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "transpose_notes", "arguments": arguments_json},
                        }
                    ]
                }
            }
        ]
    }
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n",
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    events = list(OpenAIProvider(config, credentials).stream_events([], [{"type": "function"}], None, ()))

    assert events[-1].tool_calls[0].arguments_json == arguments_json


def test_stream_events_rejects_a_tool_call_continuation_without_an_identifier(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: an unknown tool-call continuation can corrupt another call's arguments."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"transpose_notes","arguments":"{}"}}]}}]}\n\n',
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match="invalid streaming data"):
        list(OpenAIProvider(config, credentials).stream_events([], [{"type": "function"}], None, ()))


def test_stream_events_rejects_duplicate_tool_call_identifiers(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: duplicate provider call ids collapse two requested tool executions into one."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"transpose_notes","arguments":"{}"}},{"index":1,"id":"call-1","type":"function","function":{"name":"transpose_notes","arguments":"{}"}}]}}]}\n\n',
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match="invalid streaming data"):
        list(OpenAIProvider(config, credentials).stream_events([], [{"type": "function"}], None, ()))


def test_stream_events_rejects_malformed_sse_data(provider_server: _MockProviderServer) -> None:
    """Break caught: corrupt SSE JSON is silently accepted as a successful agent turn."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b"data: {not-json}\n\n",
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match="invalid streaming data"):
        list(OpenAIProvider(config, credentials).stream_events([], [], None, ()))


def test_stream_events_rejects_eof_after_a_partial_tool_call(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a truncated stream silently drops an in-progress tool call."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"transpose_","arguments":"{\\"note_ids\\":["}}]}}]}\n\n',
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match="incomplete streaming data"):
        list(OpenAIProvider(config, credentials).stream_events([], [{"type": "function"}], None, ()))


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_stream_events_never_retries_permanent_http_failures(
    provider_server: _MockProviderServer, status: int
) -> None:
    """Break caught: authentication, model, or rate-limit failures cause duplicate provider requests."""
    provider_server.responses["/v1/chat/completions"] = (
        status,
        {"Content-Type": "application/json"},
        b'{"error":"permanent failure"}',
    )
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match=str(status)):
        list(OpenAIProvider(config, credentials).stream_events([], [], None, ()))

    assert len(provider_server.requests) == 1


def test_stream_events_redacts_a_credential_from_a_streaming_failure(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: streaming errors echo a credential into an agent-visible failure."""
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")
    credential = credentials.get(config.credential_reference)
    provider_server.responses["/v1/chat/completions"] = (
        401,
        {"Content-Type": "application/json"},
        f'{{"error":"{credential}"}}'.encode(),
    )

    with pytest.raises(ProviderError) as raised:
        list(OpenAIProvider(config, credentials).stream_events([], [], None, ()))

    assert credential not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


@pytest.mark.parametrize("status", [500, 503])
def test_stream_events_caps_transient_server_retries_at_one(
    provider_server: _MockProviderServer, status: int
) -> None:
    """Break caught: repeated server failures cause an unbounded retry loop."""
    provider_server.responses["/v1/chat/completions"] = [
        (status, {"Content-Type": "application/json"}, b'{"error":"unavailable"}'),
        (status, {"Content-Type": "application/json"}, b'{"error":"unavailable"}'),
        (status, {"Content-Type": "application/json"}, b'{"error":"unavailable"}'),
    ]
    config, credentials = _config(provider_server, provider_id="openai", model_id="gpt-4o-mini")

    with pytest.raises(ProviderError, match=str(status)):
        list(OpenAIProvider(config, credentials).stream_events([], [], None, ()))

    assert len(provider_server.requests) == 2


def test_stream_chat_rejects_an_oversized_sse_line(provider_server: _MockProviderServer) -> None:
    """Break caught: a provider can make the client buffer an unbounded stream frame."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b"data: " + (b"x" * (MAX_STREAM_LINE_BYTES + 1)) + b"\n",
    )
    config, credentials = _config(provider_server, model_id="known")
    provider = OpenAICompatibleProvider(
        config,
        credentials,
        model_capabilities={"known": OpenAIProvider.CAPABILITIES["o3"]},
    )

    with pytest.raises(ProviderError, match="too large"):
        list(provider.stream_chat([{"role": "user", "content": "hello"}], [], "low", []))


def test_capabilities_are_conservative_without_explicit_provider_metadata(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: compatible model names are treated as proof of unsupported features."""
    capabilities = _provider(provider_server).get_capabilities("gpt-looks-capable")

    assert capabilities.supports_tools is False
    assert capabilities.supports_structured_output is False
    assert capabilities.supports_streaming is False


def test_cross_origin_redirect_is_rejected_without_forwarding_authorization(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a redirect can forward a bearer token to a different origin."""
    with _MockProviderServer() as redirected_server:
        provider_server.responses["/v1/models"] = (
            302,
            {"Location": f"{redirected_server.base_url}/models"},
            b"",
        )

        with pytest.raises(ProviderError, match="redirect"):
            _provider(provider_server).list_models()

        assert redirected_server.requests == []


def test_host_changing_redirect_is_rejected_without_following_it(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: a redirect can move a token from 127.0.0.1 to localhost."""
    provider_server.responses["/v1/models"] = (
        302,
        {"Location": provider_server.base_url.replace("127.0.0.1", "localhost") + "/models"},
        b"",
    )

    with pytest.raises(ProviderError, match="redirect"):
        _provider(provider_server).list_models()

    assert len(provider_server.requests) == 1


def test_deepseek_metadata_adapter_streams_its_declared_chat_model(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: DeepSeek metadata cannot drive its compatible transport request."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"content":"deep"}}]}\n\ndata: [DONE]\n\n',
    )
    config, credentials = _config(
        provider_server, provider_id="deepseek", model_id="deepseek-chat"
    )

    assert list(DeepSeekProvider(config, credentials).stream_chat([], [], None, [])) == ["deep"]
    assert json.loads(provider_server.requests[0][3])["model"] == "deepseek-chat"


def test_capability_probe_requires_a_streaming_tool_call(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: an ID-only catalog model cannot be verified for music tools."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"probe-1","type":"function","function":{"name":"music_capability_probe","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n',
    )
    config, credentials = _config(
        provider_server, provider_id="deepseek", model_id="deepseek-v4-flash"
    )

    capabilities = DeepSeekProvider(config, credentials).verify_tool_capability()

    assert capabilities.supports_streaming is True
    assert capabilities.supports_tools is True
    payload = json.loads(provider_server.requests[0][3])
    assert payload["stream"] is True
    assert "tool_choice" not in payload
    assert payload["tools"][0]["function"]["name"] == "music_capability_probe"


def test_deepseek_thinking_mode_probe_uses_automatic_tool_choice(
    provider_server: _MockProviderServer,
) -> None:
    """Break caught: DeepSeek thinking models reject a named tool_choice with HTTP 400."""
    provider_server.responses["/v1/chat/completions"] = (
        200,
        {"Content-Type": "text/event-stream"},
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"probe-thinking-1","type":"function","function":{"name":"music_capability_probe","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n',
    )
    config, credentials = _config(
        provider_server, provider_id="deepseek", model_id="deepseek-v4-pro"
    )

    capabilities = DeepSeekProvider(config, credentials).verify_tool_capability()

    assert capabilities.supports_tools is True
    payload = json.loads(provider_server.requests[0][3])
    assert "tool_choice" not in payload
    assert payload["tools"][0]["function"]["name"] == "music_capability_probe"


def test_registry_returns_registered_provider_by_id(provider_server: _MockProviderServer) -> None:
    """Break caught: configured provider ids cannot be resolved to their adapter."""
    provider = _provider(provider_server)
    registry = ModelProviderRegistry()
    registry.register("compatible", provider)

    assert registry.get("compatible") is provider
    assert isinstance(PublicProviderRegistry(), ModelProviderRegistry)


def test_normalized_image_requires_own_capability_and_reaches_wire(provider_server, tmp_path):
    from dataclasses import replace
    from PySide6.QtGui import QImage
    from ai_piano_studio.creative.media import prepare_image
    image = QImage(2, 2, QImage.Format_RGB32)
    image.fill(0xff445566)
    path = tmp_path / 'input.png'
    image.save(str(path))
    attachment = prepare_image(path)
    config, credentials = _config(provider_server, model_id='vision-test')
    capability = ModelCapabilities(True, False, False, False, True)
    provider_server.responses['/v1/chat/completions'] = (200, {'Content-Type': 'text/event-stream'}, b'data: [DONE]\n\n')
    messages = [{'role': 'user', 'content': 'theme from this image'}]
    unknown = OpenAICompatibleProvider(config, credentials, model_capabilities={'vision-test': capability})
    with pytest.raises(ProviderError, match='image input'):
        list(unknown.stream_events(messages, [], None, [attachment]))
    assert not provider_server.requests
    known = OpenAICompatibleProvider(config, credentials, model_capabilities={'vision-test': replace(capability, supports_images=True)})
    list(known.stream_events(messages, [], None, [attachment]))
    request = json.loads(provider_server.requests[-1][3])
    assert request['messages'][0]['content'][1]['image_url']['url'] == attachment.data_url
    assert messages[0]['content'] == 'theme from this image'


def test_creator_claude_accepts_images_large_stream_and_request_options(provider_server):
    config, credentials = _config(provider_server, 'anthropic', 'test-claude')
    provider = AnthropicProvider(config, credentials)
    provider.creator_enabled = True
    provider.creator_request_options = {'max_tokens': 12000}
    body = _claude_stream(tool=False, stop='end_turn').replace(b'Hello', b'x' * 70000)
    provider_server.responses['/v1/messages'] = (200, {}, body)
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Read score'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}},
    ]}]
    assert len(''.join(provider.stream_chat(messages, [], None, []))) == 70000
    request = json.loads(provider_server.requests[-1][3])
    assert request['max_tokens'] == 12000
    assert request['messages'][0]['content'][1] == {
        'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}}
