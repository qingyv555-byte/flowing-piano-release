from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlunsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.capabilities import ModelCapabilities, ReasoningOption
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.credentials import CredentialStore
from ai_piano_studio.music_agent.security import (
    ProviderEndpoint,
    SecretScrubber,
    validate_redirect_target,
)


DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 1_000_000
MAX_STREAM_LINE_BYTES = 64_000
MAX_STREAM_BYTES = 4_000_000
MAX_REDIRECTS = 3
MAX_TRANSIENT_RETRIES = 1


class ProviderError(RuntimeError):
    """A scrubbed, consumer-safe provider failure with recovery guidance."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    """A complete, provider-neutral tool call emitted at the end of a stream."""

    call_id: str
    name: str
    arguments_json: str
    thought_signature: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """An immutable normalized provider stream event."""

    text_delta: str = ""
    tool_calls: tuple[ProviderToolCall, ...] = ()
    finish_reason: str | None = None
    reasoning_content: str | None = field(default=None, repr=False)


class ModelProvider(Protocol):
    def list_models(self) -> list[str]: ...

    def get_capabilities(self, model_id: str) -> ModelCapabilities: ...

    def stream_chat(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[str]: ...

    def stream_events(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[ProviderEvent]: ...

    def test_connection(self) -> bool: ...

    def normalize_error(self, error: BaseException) -> ProviderError: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> Request | None:
        return None


class OpenAICompatibleProvider:
    """Bounded OpenAI-compatible transport with explicit model metadata."""

    CAPABILITIES: Mapping[str, ModelCapabilities] = {}
    CHAT_PATH = "/chat/completions"
    SUPPORTS_IMAGE_ENCODING = True
    _CONSERVATIVE_CAPABILITIES = ModelCapabilities(
        supports_tools=False,
        supports_structured_output=False,
        supports_audio=False,
        supports_files=False,
        supports_streaming=False,
    )

    def __init__(
        self,
        config: ProviderConfig,
        credentials: CredentialStore,
        *,
        model_capabilities: Mapping[str, ModelCapabilities] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._config = config
        self._credentials = credentials
        self._capabilities = dict(self.CAPABILITIES)
        if model_capabilities is not None:
            self._capabilities.update(model_capabilities)
        self._timeout_seconds = float(timeout_seconds)
        self._scrubber = SecretScrubber()
        self._opener: OpenerDirector = build_opener(_NoRedirect())
        self._on_progress: Callable[[ProviderProgressEvent], None] = lambda event: None

    def set_progress_callback(self, callback: Callable[[ProviderProgressEvent], None]) -> None:
        self._on_progress = callback

    def list_models(self) -> list[str]:
        try:
            with self._open("GET", "/models") as response:
                payload = self._read_json(response)
        except BaseException as error:
            raise self.normalize_error(error) from None
        try:
            models = payload["data"]
            if not isinstance(models, list):
                raise TypeError
            return [item["id"] for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)]
        except (KeyError, TypeError) as error:
            raise ProviderError("provider returned an invalid model list") from None

    def get_capabilities(self, model_id: str) -> ModelCapabilities:
        if not isinstance(model_id, str) or not model_id:
            raise ProviderError("model id is required")
        return self._capabilities.get(model_id, self._CONSERVATIVE_CAPABILITIES)

    def stream_chat(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[str]:
        events = self.stream_events(messages, tools, reasoning, attachments)
        return (event.text_delta for event in events if event.text_delta)

    def stream_events(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[ProviderEvent]:
        model_id = self._config.model_id
        if not model_id:
            raise ProviderError("model id is required for chat")
        capabilities = self.get_capabilities(model_id)
        if not capabilities.supports_streaming and not getattr(self, 'creator_enabled', False):
            raise ProviderError("selected model does not support streaming")
        if tools and not capabilities.supports_tools and not getattr(self, 'creator_enabled', False):
            raise ProviderError("selected model does not support tools")
        wire_messages = list(messages)
        if attachments:
            if ((capabilities.supports_images is not True and not getattr(self, 'creator_enabled', False))
                    or not self.SUPPORTS_IMAGE_ENCODING):
                raise ProviderError('attachments are not available: image input is unverified or unsupported for this endpoint and model')
            from ai_piano_studio.creative.media import attach_openai_image
            try:
                wire_messages = attach_openai_image(messages, attachments)
            except ValueError as error:
                raise ProviderError(str(error)) from None
        payload: dict[str, object] = {
            "model": model_id,
            "messages": wire_messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = list(tools)
        payload.update(self._reasoning_payload(reasoning, capabilities))
        wire_payload = self._wire_payload(payload)
        if getattr(self, 'creator_enabled', False):
            wire_payload.update(getattr(self, 'creator_request_options', {}))
        body = json.dumps(wire_payload, separators=(",", ":")).encode("utf-8")
        try:
            response = self._open("POST", self.CHAT_PATH, body)
        except BaseException as error:
            raise self.normalize_error(error) from None
        return self._stream_events(response)

    def test_connection(self) -> bool:
        self.list_models()
        return True

    def verify_tool_capability(self) -> ModelCapabilities:
        """Verify an unknown model can stream an inert function call."""

        model_id = self._config.model_id
        if not model_id:
            raise ProviderError("model id is required for tool capability verification")
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": "Call music_capability_probe exactly once. Do not add text.",
                }
            ],
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "music_capability_probe",
                        "description": "Verifies function-calling protocol support.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        try:
            response = self._open(
                "POST", self.CHAT_PATH, json.dumps(self._wire_payload(payload), separators=(",", ":")).encode("utf-8")
            )
            verified = False
            for event in self._stream_events(response):
                if any(call.name == "music_capability_probe" for call in event.tool_calls):
                    verified = True
        except BaseException as error:
            raise self.normalize_error(error) from None
        if verified:
            return ModelCapabilities(True, False, False, False, True)
        raise ProviderError("selected model does not support streaming tool calls")

    def normalize_error(
        self, error: BaseException, credential: str | None = None
    ) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        if isinstance(error, HTTPError):
            detail = self._bounded_error_body(error)
            message = f"provider request failed ({error.code})"
            if detail:
                message = f"{message}: {detail}"
            code = {
                401: "authentication",
                403: "authentication",
                404: "unsupported_capability",
                413: "context_too_large",
                429: "rate_limit",
            }.get(error.code, "provider_unavailable" if error.code >= 500 else "provider_error")
            return ProviderError(self._scrub(message, credential), code=code)
        if isinstance(error, (socket.timeout, TimeoutError)):
            return ProviderError("provider request timed out", code="provider_unavailable")
        if isinstance(error, URLError) and isinstance(error.reason, (socket.timeout, TimeoutError)):
            return ProviderError("provider request timed out", code="provider_unavailable")
        return ProviderError(self._scrub("provider request failed", credential), code="provider_unavailable")

    def _scrub(self, value: object, credential: str | None) -> str:
        scrubbed = self._scrubber.scrub(value)
        if credential:
            return scrubbed.replace(credential, SecretScrubber.REDACTED)
        return scrubbed

    def _reasoning_payload(
        self, reasoning: str | None, capabilities: ModelCapabilities
    ) -> dict[str, object]:
        if reasoning is None:
            return {}
        supported = {option.id for option in capabilities.reasoning_options}
        if reasoning not in supported:
            raise ProviderError("unsupported reasoning option")
        return {"reasoning_effort": reasoning}

    def _stream_events(self, response: object) -> Iterator[ProviderEvent]:
        total = 0
        tool_call_parts: dict[str, list[str]] = {}
        tool_call_indexes: dict[int, str] = {}
        finish_reason: str | None = None
        reasoning_parts: list[str] = []
        signatures: dict[str, str] = {}
        try:
            while True:
                line = response.readline() if getattr(self, 'creator_enabled', False) else response.readline(MAX_STREAM_LINE_BYTES + 1)  # type: ignore[attr-defined]
                if not line:
                    raise ProviderError("provider returned incomplete streaming data")
                total += len(line)
                if not getattr(self, 'creator_enabled', False) and (total > MAX_STREAM_BYTES or len(line) > MAX_STREAM_LINE_BYTES):
                    raise ProviderError("provider streaming response is too large")
                if not line.startswith(b"data:"):
                    continue
                raw_data = line[5:].strip()
                if raw_data == b"[DONE]":
                    tool_calls = tuple(
                        ProviderToolCall(call_id, parts[0], parts[1], signatures.get(call_id))
                        for call_id, parts in tool_call_parts.items()
                    )
                    yield ProviderEvent(tool_calls=tool_calls, finish_reason=finish_reason, reasoning_content="".join(reasoning_parts) if reasoning_parts else None)
                    return
                try:
                    event = json.loads(raw_data)
                    choices = event["choices"]
                except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
                    raise ProviderError("provider returned invalid streaming data") from None
                if not isinstance(choices, list) or not choices:
                    raise ProviderError("provider returned invalid streaming data")
                for choice in choices:
                    if not isinstance(choice, Mapping):
                        raise ProviderError("provider returned invalid streaming data")
                    delta = choice.get("delta")
                    if not isinstance(delta, Mapping):
                        raise ProviderError("provider returned invalid streaming data")
                    content = delta.get("content")
                    reasoning_content = delta.get("reasoning_content")
                    if reasoning_content is not None:
                        if not isinstance(reasoning_content, str):
                            raise ProviderError("provider returned invalid streaming data")
                        reasoning_parts.append(reasoning_content)
                    if content is not None and not isinstance(content, str):
                        raise ProviderError("provider returned invalid streaming data")
                    if content:
                        yield ProviderEvent(text_delta=content)
                    choice_finish_reason = choice.get("finish_reason")
                    if choice_finish_reason is not None:
                        if not isinstance(choice_finish_reason, str):
                            raise ProviderError("provider returned invalid streaming data")
                        finish_reason = choice_finish_reason
                    tool_calls = delta.get("tool_calls")
                    if tool_calls is not None:
                        if not isinstance(tool_calls, list):
                            raise ProviderError("provider returned invalid streaming data")
                        for tool_call in tool_calls:
                            self._accumulate_tool_call(
                                tool_call, tool_call_parts, tool_call_indexes
                            )
                            extra = tool_call.get("extra_content", {})
                            if isinstance(extra, Mapping):
                                google = extra.get("google", {})
                                if isinstance(google, Mapping) and "thought_signature" in google:
                                    signature = google["thought_signature"]
                                    if not isinstance(signature, str):
                                        raise ProviderError("provider returned invalid streaming data")
                                    signatures[tool_call_indexes[tool_call["index"]]] = signature
                if finish_reason is not None:
                    completed_tool_calls = tuple(
                        ProviderToolCall(call_id, parts[0], parts[1], signatures.get(call_id))
                        for call_id, parts in tool_call_parts.items()
                    )
                    yield ProviderEvent(
                        tool_calls=completed_tool_calls,
                        finish_reason=finish_reason,
                        reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
                    )
                    return
        except BaseException as error:
            raise self.normalize_error(error) from None
        finally:
            response.close()  # type: ignore[attr-defined]

    @staticmethod
    def _accumulate_tool_call(
        tool_call: object,
        tool_call_parts: dict[str, list[str]],
        tool_call_indexes: dict[int, str],
    ) -> None:
        if not isinstance(tool_call, Mapping):
            raise ProviderError("provider returned invalid streaming data")
        index = tool_call.get("index")
        if not isinstance(index, int) or index < 0:
            raise ProviderError("provider returned invalid streaming data")
        call_id = tool_call.get("id")
        if call_id is not None and (not isinstance(call_id, str) or not call_id):
            raise ProviderError("provider returned invalid streaming data")
        known_call_id = tool_call_indexes.get(index)
        if call_id is None:
            if known_call_id is None:
                raise ProviderError("provider returned invalid streaming data")
            call_id = known_call_id
        elif known_call_id is not None and known_call_id != call_id:
            raise ProviderError("provider returned invalid streaming data")
        elif call_id in tool_call_parts and known_call_id != call_id:
            raise ProviderError("provider returned invalid streaming data")
        tool_call_indexes[index] = call_id
        parts = tool_call_parts.setdefault(call_id, ["", ""])
        kind = tool_call.get("type")
        if kind is not None and kind != "function":
            raise ProviderError("provider returned invalid streaming data")
        function = tool_call.get("function", {})
        if not isinstance(function, Mapping):
            raise ProviderError("provider returned invalid streaming data")
        name = function.get("name")
        arguments = function.get("arguments")
        if name is not None:
            if not isinstance(name, str):
                raise ProviderError("provider returned invalid streaming data")
            parts[0] += name
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ProviderError("provider returned invalid streaming data")
            parts[1] += arguments

    def _read_json(self, response: object) -> object:
        content_length = response.headers.get("Content-Length")  # type: ignore[attr-defined]
        if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
            raise ProviderError("provider response is too large")
        raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("provider response is too large")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError("provider returned invalid JSON") from None

    def _bounded_error_body(self, error: HTTPError) -> str:
        try:
            raw = error.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            return ""
        if len(raw) > MAX_RESPONSE_BYTES:
            return "response too large"
        return raw.decode("utf-8", errors="replace")

    def _open(self, method: str, path: str, body: bytes | None = None) -> object:
        endpoint = self._config.endpoint
        url = self._url_for(endpoint, path)
        redirects = 0
        transient_retries = 0
        while redirects <= MAX_REDIRECTS:
            credential = self._credentials.get(self._config.credential_reference)
            request = Request(
                url,
                data=body,
                method=method,
                headers=self._request_headers(credential),
            )
            try:
                self._on_progress(ProviderProgressEvent("connecting"))
                response = self._opener.open(request, timeout=self._timeout_seconds)
                if method == "POST":
                    self._on_progress(ProviderProgressEvent("submitted", status="waiting"))
                return response
            except HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    if not location:
                        raise ProviderError("provider redirect is invalid") from None
                    try:
                        target = validate_redirect_target(endpoint, urljoin(url, location))
                    except ValueError:
                        raise ProviderError("provider redirect is not allowed") from None
                    url = target.url
                    redirects += 1
                    continue
                if 500 <= error.code < 600 and transient_retries < MAX_TRANSIENT_RETRIES:
                    transient_retries += 1
                    error.close()
                    continue
                raise self.normalize_error(error, credential) from None
            except BaseException as error:
                if (
                    isinstance(error, (socket.timeout, TimeoutError, URLError))
                    and transient_retries < MAX_TRANSIENT_RETRIES
                ):
                    transient_retries += 1
                    continue
                raise self.normalize_error(error, credential) from None
        raise ProviderError("provider redirected too many times")

    def _request_headers(self, credential: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

    def _wire_payload(self, payload: dict[str, object]) -> dict[str, object]:
        return payload

    @staticmethod
    def _url_for(endpoint: ProviderEndpoint, path: str) -> str:
        netloc = endpoint.host if endpoint.port is None else f"{endpoint.host}:{endpoint.port}"
        return urlunsplit((endpoint.scheme, netloc, f"{endpoint.base_path}{path}", "", ""))


class OpenAIProvider(OpenAICompatibleProvider):
    CAPABILITIES = {
        "gpt-4o": ModelCapabilities(True, True, False, False, True),
        "gpt-4o-mini": ModelCapabilities(True, True, False, False, True),
        "o3": ModelCapabilities(
            True,
            True,
            False,
            False,
            True,
            (
                ReasoningOption("low", "Low", "Uses less reasoning effort."),
                ReasoningOption("medium", "Medium", "Uses standard reasoning effort."),
                ReasoningOption("high", "High", "Uses more reasoning effort."),
            ),
        ),
    }


class DeepSeekProvider(OpenAICompatibleProvider):
    CAPABILITIES = {
        "deepseek-chat": ModelCapabilities(True, False, False, False, True),
        "deepseek-reasoner": ModelCapabilities(False, False, False, False, True),
    }
