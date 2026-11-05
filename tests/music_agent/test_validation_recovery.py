from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
import json
from math import isclose

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoop
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.providers import (
    OpenAICompatibleProvider,
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
from ai_piano_studio.music_agent.validation_recovery import (
    ToolArgumentNormalizer,
    ToolErrorClass,
    classify_tool_result,
)


@dataclass
class _FakeProvider:
    responses: list[tuple[ProviderEvent, ...]]

    def __post_init__(self) -> None:
        self.requests: list[list[Mapping[str, object]]] = []

    def get_capabilities(self, _model_id: str) -> ModelCapabilities:
        return ModelCapabilities(True, True, False, False, True)

    def stream_events(
        self,
        messages: Sequence[Mapping[str, object]],
        _tools: Sequence[Mapping[str, object]],
        _reasoning: str | None,
        _attachments: Sequence[object],
    ) -> Iterator[ProviderEvent]:
        self.requests.append(list(messages))
        yield from self.responses.pop(0)


class _FakeSseResponse:
    def __init__(self, body: bytes) -> None:
        self._lines = iter(body.splitlines(keepends=True))

    def readline(self, _limit: int) -> bytes:
        return next(self._lines, b"")

    def close(self) -> None:
        return


class _FragmentedProvider:
    """Fake OpenAI-compatible transport with fragmented function arguments."""

    def __init__(self, responses: list[bytes]) -> None:
        credentials = MemoryCredentialStore()
        reference = credentials.put("compatible", "fake-key")
        config = ProviderConfig(
            provider_id="compatible",
            base_url="http://127.0.0.1:1/v1",
            credential_reference=reference,
            model_id="fake-streaming",
            allow_local_http=True,
        )
        self._responses = list(responses)
        self.requests: list[list[Mapping[str, object]]] = []
        self._transport = OpenAICompatibleProvider(
            config,
            credentials,
            model_capabilities={
                "fake-streaming": ModelCapabilities(True, True, False, False, True)
            },
        )
        self._transport._open = self._open  # type: ignore[method-assign]

    def _open(self, _method: str, _path: str, _body: bytes | None = None) -> _FakeSseResponse:
        return _FakeSseResponse(self._responses.pop(0))

    def get_capabilities(self, model_id: str) -> ModelCapabilities:
        return self._transport.get_capabilities(model_id)

    def stream_events(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[ProviderEvent]:
        self.requests.append(list(messages))
        yield from self._transport.stream_events(messages, tools, reasoning, attachments)


def _sse_event(delta: Mapping[str, object], finish_reason: str | None = None) -> bytes:
    choice: dict[str, object] = {"delta": dict(delta)}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return b"data: " + json.dumps({"choices": [choice]}).encode() + b"\n\n"


def test_repairable_validation_keeps_one_collaboration_transaction_for_corrected_write() -> None:
    """A validation failure must not close the turn before the model can repair it."""

    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "read",
                            "get_notes",
                            '{"start_bar":1,"end_bar":1}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid-write",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "corrected-write",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )
    loop = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    )

    outcome = loop.run("读取后添加一个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "finished"
    assert len(provider.requests) == 4
    assert len(editor.state.active_notes) == 1
    assert editor._history.undo_depth == 1
    assert [result.error_code for result in outcome.tool_results] == [
        None,
        "validation_error",
        None,
    ]


def test_model_repair_receives_specific_feedback_and_one_extra_provider_call() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_NOTES, MusicPermission.WRITE_NOTES}),
    )
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("read", "get_notes", '{"start_bar":1,"end_bar":1}'),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "corrected",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )
    loop = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    )

    outcome = loop.run("读取后添加一个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 4
    feedback_messages = [
        message
        for message in provider.requests[2]
        if message.get("role") == "tool"
    ]
    feedback = next(
        message for message in feedback_messages if message.get("tool_call_id") == "invalid"
    )
    payload = json.loads(str(feedback["content"]))
    assert payload["success"] is False
    assert payload["error_code"] == "validation_error"
    assert payload["data"]["invalid_items"] == [
        {
            "item_index": 0,
            "field": "duration",
            "reason": "note_duration_too_long",
        }
    ]
    assert payload["data"]["valid_item_count"] == 0
    assert payload["data"]["preserve_valid_items"] is True
    retry_messages = provider.requests[2]
    assert retry_messages[-2]["role"] == "assistant"
    assert retry_messages[-2]["tool_calls"][0]["id"] == "invalid"
    assert retry_messages[-1]["role"] == "tool"
    assert retry_messages[-1]["tool_call_id"] == "invalid"
    assert outcome.debug_snapshot["provider_calls"] == 4
    assert outcome.debug_snapshot["validation_model_repairs"] == 1
    assert outcome.debug_snapshot["local_repairs"] == 0


def test_integer_like_arguments_are_repaired_locally_without_a_provider_retry() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset({MusicPermission.WRITE_BPM}),
    )
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(ProviderToolCall("bpm", "set_bpm", '{"bpm":"120"}'),)
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )
    loop = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    )

    outcome = loop.run("设置 BPM。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 2
    assert outcome.tool_results[0].success is True
    assert outcome.debug_snapshot["provider_calls"] == 2
    assert outcome.debug_snapshot["validation_model_repairs"] == 0
    assert outcome.debug_snapshot["local_repairs"] == 1


def test_same_validation_fingerprint_stops_after_one_model_repair() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(ProviderToolCall("bad-1", "add_notes", "{}"),)
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(ProviderToolCall("bad-2", "add_notes", "{}"),)
                ),
            ),
            (ProviderEvent(text_delta="不应消费。", finish_reason="stop"),),
        ]
    )
    loop = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    )

    outcome = loop.run("添加音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "validation_error"
    assert len(provider.requests) == 2
    assert len(outcome.tool_results) == 2
    assert outcome.debug_snapshot["validation_model_repairs"] == 1


def test_validation_classes_keep_terminal_errors_out_of_model_retry() -> None:
    local = MusicToolResult.failure(
        "set_bpm",
        "validation_error",
        "integer-like value",
        data={
            "invalid_items": [
                {"item_index": 0, "field": "bpm", "reason": "integer_coercion"}
            ]
        },
    )
    semantic = MusicToolResult.failure(
        "add_notes",
        "validation_error",
        "duration is too long",
        data={
            "invalid_items": [
                {"item_index": 0, "field": "duration", "reason": "note_duration_too_long"}
            ]
        },
    )
    existing_note_duplicate = MusicToolResult.failure(
        "add_notes",
        "validation_error",
        "duplicate overlapping note at the same pitch and time",
        data={
            "invalid_items": [
                {"item_index": 0, "field": "start_time", "reason": "duplicate_note"}
            ]
        },
    )
    missing_note = MusicToolResult.failure("update_notes", "not_found", "note id is not available")
    permission = MusicToolResult.failure("add_notes", "permission_denied", "permission is not granted")

    assert classify_tool_result(local) is ToolErrorClass.LOCAL_REPAIRABLE
    assert classify_tool_result(semantic) is ToolErrorClass.MODEL_REPAIRABLE
    assert classify_tool_result(existing_note_duplicate) is ToolErrorClass.MODEL_REPAIRABLE
    assert classify_tool_result(missing_note) is ToolErrorClass.MODEL_REPAIRABLE
    assert classify_tool_result(permission) is ToolErrorClass.TERMINAL


def test_argument_normalizer_repairs_only_mechanical_values() -> None:
    normalizer = ToolArgumentNormalizer(MusicToolRegistry.default())

    bpm = normalizer.normalize("set_bpm", {"bpm": "120"})
    assert bpm.changed is True
    assert bpm.arguments == {"bpm": 120}

    notes = {
        "notes": [
            {"pitch": 60, "start_time": 1.0, "duration": 0.5, "velocity": 80},
            {"pitch": 60, "start_time": 1.0, "duration": 0.5, "velocity": 80},
        ]
    }
    deduplicated = normalizer.normalize("add_notes", notes)
    assert deduplicated.changed is True
    assert len(deduplicated.arguments["notes"]) == 1

    semantic_pitch = normalizer.normalize(
        "add_notes",
        {"notes": [{"pitch": 130, "start_time": 0.0, "duration": 0.5, "velocity": 80}]},
    )
    assert semantic_pitch.arguments["notes"][0]["pitch"] == 130


def test_argument_normalizer_snaps_only_an_authorized_boundary_epsilon() -> None:
    normalizer = ToolArgumentNormalizer(MusicToolRegistry.default())
    args = {
        "notes": [
            {"pitch": 60, "start_time": 0.0, "duration": 2.0 + 5e-10, "velocity": 80}
        ]
    }
    normalized = normalizer.normalize(
        "add_notes",
        args,
        authorized_bars=(BarRange(1, 1),),
        bar_duration_seconds=2.0,
    )
    assert normalized.changed is True
    assert isclose(normalized.arguments["notes"][0]["duration"], 2.0)

    outside = normalizer.normalize(
        "add_notes",
        {"notes": [{"pitch": 130, "start_time": 0.0, "duration": 0.5, "velocity": 80}]},
        authorized_bars=(BarRange(1, 1),),
        bar_duration_seconds=2.0,
    )
    assert outside.arguments["notes"][0]["pitch"] == 130


def test_model_repair_merges_a_partial_correction_without_losing_valid_notes() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    original_notes = [
        {"pitch": 60, "start_time": 0.25, "duration": 0.5, "velocity": 80},
        {"pitch": 62, "start_time": 0.75, "duration": 0.5, "velocity": 80},
        {"pitch": 64, "start_time": 2.25, "duration": 10000, "velocity": 80},
        {"pitch": 65, "start_time": 2.75, "duration": 10000, "velocity": 80},
    ]
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid",
                            "add_notes",
                            json.dumps({"notes": original_notes}),
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "corrected",
                            "add_notes",
                            json.dumps(
                                {
                                    "notes": [
                                        {"pitch": 64, "start_time": 2.25, "duration": 0.5, "velocity": 80},
                                        {"pitch": 65, "start_time": 2.75, "duration": 0.5, "velocity": 80},
                                    ]
                                }
                            ),
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("添加这组音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 3
    assert outcome.tool_results[-1].success is True
    assert outcome.tool_results[-1].added_notes == 4
    assert outcome.proposal is not None
    assert len(outcome.proposal.operations[0].payload["notes"]) == 4
    assert outcome.debug_snapshot["validation_model_repairs"] == 1


def test_terminal_permission_failure_does_not_consume_a_provider_retry() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),), permissions=frozenset()
    )
    provider = _FakeProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("denied", "add_notes", "{}"),)),),
            (ProviderEvent(text_delta="不应被调用。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("添加音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "permission_denied"
    assert len(provider.requests) == 1
    assert outcome.debug_snapshot["validation_model_repairs"] == 0


def test_budget_failure_does_not_consume_a_provider_retry() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(),
        permissions=frozenset({MusicPermission.WRITE_BPM}),
        limits=MusicAgentLimits(max_write_calls=1),
    )
    provider = _FakeProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("first", "set_bpm", '{"bpm":120}'),)),),
            (ProviderEvent(tool_calls=(ProviderToolCall("second", "set_bpm", '{"bpm":121}'),)),),
            (ProviderEvent(text_delta="不应被调用。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("连续设置速度。", context, intent="EDIT_MUSIC")

    assert outcome.status == "limit_exceeded"
    assert len(provider.requests) == 2
    assert outcome.debug_snapshot["validation_model_repairs"] == 0


def test_stale_revision_conflict_does_not_consume_a_provider_retry() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    current = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    stale = replace(current, baseline_revision=current.baseline_revision + 1)
    provider = _FakeProvider(
        [
            (ProviderEvent(tool_calls=(ProviderToolCall("stale", "add_notes", "{}"),)),),
            (ProviderEvent(text_delta="不应被调用。", finish_reason="stop"),),
        ]
    )

    class DirectContextBuilder:
        def build(self, instruction: str, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
            return [{"role": "user", "content": instruction}]

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=DirectContextBuilder(),
        model_id="fake",
    ).run("添加音符。", stale, intent="EDIT_MUSIC")

    assert outcome.status == "conflict"
    assert len(provider.requests) == 1
    assert outcome.debug_snapshot["validation_model_repairs"] == 0


def test_66_note_case_a_repairs_three_mechanical_duplicates_without_provider_retry() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 20),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    unique_notes = [
        {"pitch": 48 + index % 36, "start_time": index * 0.1, "duration": 0.25, "velocity": 72}
        for index in range(63)
    ]
    notes = unique_notes + unique_notes[:3]
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(ProviderToolCall("local", "add_notes", json.dumps({"notes": notes})),)
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("暂存这组旋律。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 2
    assert outcome.tool_results[0].success is True
    assert outcome.tool_results[0].added_notes == 63
    assert outcome.debug_snapshot["local_repairs"] == 3
    assert outcome.debug_snapshot["validation_model_repairs"] == 0


def test_66_note_case_b_repairs_three_semantic_items_with_one_extra_provider_call() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 20),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    valid_notes = [
        {"pitch": 48 + index % 36, "start_time": index * 0.1, "duration": 0.25, "velocity": 72}
        for index in range(63)
    ]
    invalid_notes = [
        {"pitch": 90 + index, "start_time": 31.0 + index * 0.25, "duration": 10000, "velocity": 72}
        for index in range(3)
    ]
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid",
                            "add_notes",
                            json.dumps({"notes": valid_notes + invalid_notes}),
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "repair",
                            "add_notes",
                            json.dumps(
                                {
                                    "notes": [
                                        {"pitch": 90 + index, "start_time": 31.0 + index * 0.25, "duration": 0.25, "velocity": 72}
                                        for index in range(3)
                                    ]
                                }
                            ),
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已暂存。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("暂存这组旋律。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 3
    assert outcome.tool_results[-1].success is True
    assert outcome.tool_results[-1].added_notes == 66
    assert outcome.debug_snapshot["provider_calls"] == 3
    assert outcome.debug_snapshot["validation_model_repairs"] == 1
    assert outcome.debug_snapshot["local_repairs"] == 0


def test_collaboration_second_write_recovery_commits_one_history_transaction() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "first",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid",
                            "add_notes",
                            '{"notes":[{"pitch":62,"start_time":0.75,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "corrected",
                            "add_notes",
                            '{"notes":[{"pitch":62,"start_time":0.75,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (ProviderEvent(text_delta="已完成。", finish_reason="stop"),),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("添加两个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "finished"
    assert len(editor.state.active_notes) == 2
    assert editor._history.undo_depth == 1
    assert len(provider.requests) == 4


def test_final_repair_failure_rolls_back_the_whole_collaboration_turn() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 2),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
        mode=MusicExecutionMode.COLLABORATION,
    )
    gateway.begin_turn(context)
    before = editor.state
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "first",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":0.5,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid-1",
                            "add_notes",
                            '{"notes":[{"pitch":62,"start_time":0.75,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "invalid-2",
                            "add_notes",
                            '{"notes":[{"pitch":62,"start_time":0.75,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("添加两个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "validation_error"
    assert len(provider.requests) == 3
    assert editor.state == before
    assert editor._history.undo_depth == 0


def test_schema_validation_feedback_lists_each_bad_note_and_preserves_valid_count() -> None:
    gateway = MusicAgentToolGateway(EditorController(ProjectState()))
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "add_notes",
        {
            "notes": [
                {"pitch": 60, "start_time": 0.25, "duration": 0.5, "velocity": 80},
                {"pitch": 130, "start_time": 0.75, "duration": 0.5, "velocity": 80},
                {"pitch": 62, "start_time": 1.25, "duration": 0.5, "velocity": 200},
                {"pitch": 64, "start_time": 1.75, "velocity": 80},
            ]
        },
        context,
    )

    assert result.error_code == "validation_error"
    assert result.data["valid_item_count"] == 1
    assert result.data["total_item_count"] == 4
    assert [item["item_index"] for item in result.data["invalid_items"]] == [1, 2, 3]
    assert [(item["field"], item["reason"]) for item in result.data["invalid_items"]] == [
        ("pitch", "value_above_maximum"),
        ("velocity", "value_above_maximum"),
        ("note", "missing_required_field"),
    ]


def test_66_note_case_c_stops_after_one_repeated_invalid_repair_without_a_proposal() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 20),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    valid_notes = [
        {"pitch": 48 + index % 36, "start_time": index * 0.1, "duration": 0.25, "velocity": 72}
        for index in range(63)
    ]
    invalid_notes = [
        {"pitch": 90 + index, "start_time": 31.0 + index * 0.25, "duration": 10_000, "velocity": 72}
        for index in range(3)
    ]
    original = valid_notes + invalid_notes
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall("invalid", "add_notes", json.dumps({"notes": original})),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "still-invalid",
                            "add_notes",
                            json.dumps(
                                {
                                    "notes": [
                                        {
                                            "pitch": 90 + index,
                                            "start_time": 31.0 + index * 0.25,
                                            "duration": 10_000,
                                            "velocity": 72,
                                        }
                                        for index in range(3)
                                    ]
                                }
                            ),
                        ),
                    )
                ),
            ),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("暂存这组旋律。", context, intent="EDIT_MUSIC")

    assert outcome.status == "validation_error"
    assert len(provider.requests) == 2
    assert outcome.proposal is None
    assert outcome.debug_snapshot["validation_model_repairs"] == 1
    assert len(editor.state.active_notes) == 0


def test_proposal_validation_failure_releases_the_unsealed_workspace() -> None:
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )
    provider = _FakeProvider(
        [
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "bad",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
            (
                ProviderEvent(
                    tool_calls=(
                        ProviderToolCall(
                            "still-bad",
                            "add_notes",
                            '{"notes":[{"pitch":60,"start_time":0.25,"duration":10000,"velocity":80}]}',
                        ),
                    )
                ),
            ),
        ]
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake",
    ).run("添加一个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "validation_error"
    assert outcome.proposal is None
    assert context.turn_id not in gateway._proposal_workspaces


def test_fragmented_streaming_tool_arguments_enter_the_same_recovery_turn() -> None:
    invalid_first = b"".join(
        (
            _sse_event(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "invalid",
                            "type": "function",
                            "function": {
                                "name": "add_",
                                "arguments": '{"notes":[{"pitch":60,"start_time":0.25,"duration":10000',
                            },
                        }
                    ]
                }
            ),
            _sse_event(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {
                                "name": "notes",
                                "arguments": ',"velocity":80}]}',
                            },
                        }
                    ]
                }
            ),
            b"data: [DONE]\n\n",
        )
    )
    corrected = b"".join(
        (
            _sse_event(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "corrected",
                            "type": "function",
                            "function": {
                                "name": "add_notes",
                                "arguments": '{"notes":[{"pitch":60,"start_time":0.25,"duration":0.5,"velocity":80}]}',
                            },
                        }
                    ]
                }
            ),
            b"data: [DONE]\n\n",
        )
    )
    final = b"".join((_sse_event({"content": "已完成。"}, "stop"),))
    provider = _FragmentedProvider([invalid_first, corrected, final])
    editor = EditorController(ProjectState())
    gateway = MusicAgentToolGateway(editor)
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    outcome = MusicAgentLoop(
        provider=provider,
        registry=MusicToolRegistry.default(),
        gateway=gateway,
        context_builder=MusicAgentContextBuilder(gateway),
        model_id="fake-streaming",
    ).run("添加一个音符。", context, intent="EDIT_MUSIC")

    assert outcome.status == "proposal"
    assert len(provider.requests) == 3
    assert outcome.tool_results[0].error_code == "validation_error"
    assert outcome.tool_results[1].success is True
    assert outcome.debug_snapshot["provider_calls"] == 3


def test_missing_note_feedback_identifies_the_repairable_identity_error() -> None:
    gateway = MusicAgentToolGateway(EditorController(ProjectState()))
    context = gateway.create_execution_context(
        authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.WRITE_NOTES}),
    )

    result = gateway.execute(
        "update_notes",
        {"updates": [{"id": "missing", "velocity": 100}]},
        context,
    )

    assert result.error_code == "not_found"
    assert result.data["invalid_items"] == [
        {"field": "id", "reason": "note_id_not_found"}
    ]
