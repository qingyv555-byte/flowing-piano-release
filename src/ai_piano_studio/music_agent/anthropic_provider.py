"""Claude Messages transport using the shared bounded, credential-safe HTTP layer."""

from __future__ import annotations

import json
from collections.abc import Iterator
from urllib.parse import quote

from ai_piano_studio.music_agent.providers import (
    MAX_STREAM_BYTES,
    MAX_STREAM_LINE_BYTES,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderEvent,
    ProviderToolCall,
)


class AnthropicProvider(OpenAICompatibleProvider):
    CHAT_PATH = "/messages"
    SUPPORTS_IMAGE_ENCODING = True

    def _request_headers(self, credential: str) -> dict[str, str]:
        return {
            "x-api-key": credential,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        models: list[str] = []
        cursor = ""
        seen: set[str] = set()
        try:
            for _ in range(20):
                path = "/models?limit=100" + ("&after_id=" + quote(cursor, safe="") if cursor else "")
                with self._open("GET", path) as response:
                    payload = self._read_json(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ProviderError("provider returned an invalid model list")
                for item in payload["data"]:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        raise ProviderError("provider returned an invalid model list")
                    if item["id"] not in models:
                        models.append(item["id"])
                if not payload.get("has_more", False):
                    return models
                cursor = payload.get("last_id")
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise ProviderError("provider returned an invalid model cursor")
                seen.add(cursor)
            raise ProviderError("provider model list is too large")
        except Exception as error:
            raise self.normalize_error(error) from None

    def _wire_payload(self, payload: dict[str, object]) -> dict[str, object]:
        messages: list[dict[str, object]] = []
        system: list[str] = []
        for message in payload["messages"]:
            role = message["role"]
            content = message.get("content") or ""
            if not isinstance(content, (str, list)):
                raise ProviderError("unsupported message content")
            if role == "system":
                system.append(content)
                continue
            blocks: list[dict[str, object]] = []
            if role == "tool":
                role = "user"
                blocks.append({"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content})
            else:
                if role not in {"user", "assistant"}:
                    raise ProviderError("unsupported message role")
                if isinstance(content, list):
                    for part in content:
                        if part.get('type') == 'text':
                            blocks.append(dict(type='text', text=part['text']))
                        elif part.get('type') == 'image_url':
                            url = part['image_url']['url']
                            if url.startswith('data:') and ';base64,' in url:
                                media_type, data = url[5:].split(';base64,', 1)
                                blocks.append(dict(type='image', source=dict(type='base64', media_type=media_type, data=data)))
                            else:
                                blocks.append(dict(type='image', source=dict(type='url', url=url)))
                        else:
                            raise ProviderError('unsupported message content')
                elif content:
                    blocks.append({"type": "text", "text": content})
                for call in message.get("tool_calls", []):
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                    except (KeyError, TypeError, ValueError):
                        raise ProviderError("invalid tool transcript") from None
                    if not isinstance(arguments, dict):
                        raise ProviderError("invalid tool transcript")
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": arguments})
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        result = {"model": payload["model"], "messages": messages, "max_tokens": 4096, "stream": True}
        if system:
            result["system"] = "\n\n".join(system)
        if payload.get("tools"):
            result["tools"] = [
                {"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                 "input_schema": tool["function"]["parameters"]}
                for tool in payload["tools"]
            ]
        if "reasoning_effort" in payload:
            raise ProviderError("unsupported reasoning option")
        return result

    def _stream_events(self, response: object) -> Iterator[ProviderEvent]:
        total = 0
        blocks: dict[int, dict[str, object]] = {}
        closed: set[int] = set()
        finish = None
        started = False
        try:
            while True:
                line = response.readline() if getattr(self, 'creator_enabled', False) else response.readline(MAX_STREAM_LINE_BYTES + 1)
                if not line:
                    raise ProviderError("provider returned incomplete streaming data")
                total += len(line)
                if not getattr(self, 'creator_enabled', False) and (total > MAX_STREAM_BYTES or len(line) > MAX_STREAM_LINE_BYTES):
                    raise ProviderError("provider streaming response is too large")
                if not line.startswith(b"data:"):
                    continue
                event = json.loads(line[5:].strip())
                kind = event["type"]
                if kind == "error":
                    # Do not forward server diagnostics (which may echo request data).
                    raise ProviderError("provider stream failed", code="provider_unavailable")
                if kind == "message_start":
                    if started:
                        raise ProviderError("provider returned invalid streaming data")
                    started = True
                elif kind == "content_block_start":
                    index = event["index"]
                    block = event["content_block"]
                    if not started or not isinstance(index, int) or index < 0 or index in blocks:
                        raise ProviderError("provider returned invalid streaming data")
                    if block["type"] not in {"text", "tool_use"}:
                        raise ProviderError("unsupported provider content block")
                    blocks[index] = {**block, "partial_json": ""}
                    if block["type"] == "text" and block.get("text"):
                        yield ProviderEvent(text_delta=block["text"])
                elif kind == "content_block_delta":
                    index = event["index"]
                    if index in closed:
                        raise ProviderError("provider returned invalid streaming data")
                    block = blocks[index]
                    delta = event["delta"]
                    if delta["type"] == "text_delta" and block["type"] == "text":
                        if not isinstance(delta["text"], str):
                            raise ProviderError("provider returned invalid streaming data")
                        yield ProviderEvent(text_delta=delta["text"])
                    elif delta["type"] == "input_json_delta" and block["type"] == "tool_use":
                        block["partial_json"] += delta["partial_json"]
                    else:
                        raise ProviderError("unsupported provider content delta")
                elif kind == "content_block_stop":
                    index = event["index"]
                    if index not in blocks or index in closed:
                        raise ProviderError("provider returned invalid streaming data")
                    closed.add(index)
                elif kind == "message_delta":
                    finish = event["delta"].get("stop_reason")
                elif kind == "message_stop":
                    if not started or set(blocks) != closed or finish not in {"end_turn", "tool_use", "stop_sequence"}:
                        raise ProviderError("provider returned incomplete streaming data")
                    calls = []
                    for block in blocks.values():
                        if block["type"] != "tool_use":
                            continue
                        call_id, name = block.get("id"), block.get("name")
                        arguments = json.loads(block["partial_json"]) if block["partial_json"] else block.get("input", {})
                        if not isinstance(arguments, dict) or not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                            raise ProviderError("provider returned invalid tool data")
                        calls.append(ProviderToolCall(call_id, name, json.dumps(arguments)))
                    if (bool(calls) != (finish == "tool_use")) or len({call.call_id for call in calls}) != len(calls):
                        raise ProviderError("provider returned invalid tool data")
                    yield ProviderEvent(tool_calls=tuple(calls), finish_reason=finish)
                    return
                # Ping and future non-content events do not affect the transcript.
        except GeneratorExit:
            raise
        except Exception as error:
            raise self.normalize_error(error) from None
        finally:
            response.close()
