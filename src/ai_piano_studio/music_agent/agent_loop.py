"""Provider-agnostic bounded tool loop for the AI Piano Studio assistant."""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from ai_piano_studio.music_agent.capabilities import AssistantCapabilitySet, ModelCapabilities
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.providers import ProviderError, ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.provider_feedback import (
    MAX_PROVIDER_FEEDBACK_BYTES,
    serialize_provider_feedback,
)
from ai_piano_studio.music_agent.tool_contracts import (
    BarRange,
    MusicAgentExecutionContext,
    MusicEditProposal,
    MusicExecutionMode,
    MusicToolResult,
)
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from ai_piano_studio.music_agent.tool_registry import MusicToolRegistry
from ai_piano_studio.music_agent.validation_recovery import (
    ToolArgumentNormalizer,
    ToolErrorClass,
    classify_tool_result,
    validation_fingerprints,
)


logger = logging.getLogger(__name__)

MAX_TOOL_FEEDBACK_BYTES = MAX_PROVIDER_FEEDBACK_BYTES
MAX_SOURCE_TEMPO_REPAIR_ROUNDS = 2  # cached image reread, then metadata confirmation
_SOURCE_TEMPO_ENFORCEMENT_MESSAGE = (
    "Internal completion check: this visual-score reconstruction has notes but no successful set_bpm. "
    "Read the actual source tempo and call set_bpm to confirm it, even if it is 120. "
    "This isolated draft preserves authored note seconds when setting BPM. Do not add, rewrite, "
    "duplicate or retime any notes. If native image context was reset, inspect the same cached "
    "score source again before reading its tempo. Confirm the BPM now, then finish. "
    "If the source tempo cannot be determined, report that the reconstruction is incomplete."
)


@dataclass(frozen=True, slots=True)
class MusicAgentLoopOutcome:
    """UI-safe result of one bounded assistant turn."""

    status: str
    text: str
    context: MusicAgentExecutionContext
    proposal: MusicEditProposal | None = None
    tool_results: tuple[MusicToolResult, ...] = ()
    tool_call_count: int = 0
    read_count: int = 0
    write_count: int = 0
    debug_snapshot: Mapping[str, object] = field(default_factory=dict)


class MusicAgentLoop:
    """Run normalized provider events through the registered music tool Gateway only."""

    def __init__(
        self,
        *,
        provider: object,
        registry: MusicToolRegistry,
        gateway: MusicAgentToolGateway,
        context_builder: MusicAgentContextBuilder,
        model_id: str,
        is_cancelled: Callable[[], bool] | None = None,
        on_text_delta: Callable[[str], None] | None = None,
        on_response_started: Callable[[], None] | None = None,
        on_validation_repair: Callable[[int], None] | None = None,
        on_progress: Callable[[ProviderProgressEvent], None] | None = None,
        take_pending_messages: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        if not isinstance(registry, MusicToolRegistry):
            raise ValueError("registry must be MusicToolRegistry")
        if not isinstance(gateway, MusicAgentToolGateway):
            raise ValueError("gateway must be MusicAgentToolGateway")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        self._provider = provider
        self._registry = registry
        self._gateway = gateway
        self._context_builder = context_builder
        self._model_id = model_id
        self._is_cancelled = is_cancelled or (lambda: False)
        self._on_text_delta = on_text_delta
        self._on_response_started = on_response_started
        self._on_validation_repair = on_validation_repair
        self._on_progress = on_progress
        self._take_pending_messages = take_pending_messages or (lambda: ())
        setter = getattr(provider, "set_progress_callback", None)
        if callable(setter):
            setter(self._progress)
        self._argument_normalizer = ToolArgumentNormalizer(registry)

    def _progress(self, event: ProviderProgressEvent) -> None:
        if self._on_progress is not None:
            self._on_progress(event)

    def run(
        self,
        instruction: str,
        context: MusicAgentExecutionContext,
        *,
        reasoning: str | None = None,
        attachments: Sequence[object] = (),
        requested_bars: tuple[BarRange, ...] = (),
        intent: str = "READ_MUSIC",
        intent_template: str = "",
        conversation_history: Sequence[Mapping[str, object]] = (),
    ) -> MusicAgentLoopOutcome:
        """A task owns its transport, including a suspended native tool call."""
        try:
            return self._run(instruction, context, reasoning=reasoning, attachments=attachments,
                requested_bars=requested_bars, intent=intent, intent_template=intent_template,
                conversation_history=conversation_history)
        finally:
            close = getattr(self._provider, "close", None)
            if callable(close):
                close()

    def _run(
        self,
        instruction: str,
        context: MusicAgentExecutionContext,
        *,
        reasoning: str | None = None,
        attachments: Sequence[object] = (),
        requested_bars: tuple[BarRange, ...] = (),
        intent: str = "READ_MUSIC",
        intent_template: str = "",
        conversation_history: Sequence[Mapping[str, object]] = (),
    ) -> MusicAgentLoopOutcome:
        """Run until completion, staged write, cancellation, provider error, or a shared limit."""
        from ai_piano_studio.music_agent.creator_tools import CREATOR_TEMPLATE
        if context.intent_template == CREATOR_TEMPLATE:
            from ai_piano_studio.music_agent.creator_loop import run_creator
            return run_creator(self, instruction, context, reasoning=reasoning,
                               attachments=attachments, conversation_history=conversation_history)
        if context.intent_template == 'source_discovery':
            intent_template = 'source_discovery'
        self._progress(ProviderProgressEvent("understanding"))
        try:
            capabilities = self._capabilities()
        except ProviderError as error:
            logger.warning("Music provider returned a safe ProviderError")
            return self._terminal_outcome(error.code, (), context, ())
        except Exception:
            logger.warning("Music provider capability lookup failed")
            return self._terminal_outcome("provider_error", (), context, ())
        debug_steps: list[dict[str, object]] = []
        app_capabilities = AssistantCapabilitySet.from_registry(self._registry)
        try:
            messages = self._context_builder.build(
                instruction,
                context,
                requested_bars=requested_bars,
                intent=intent,
                intent_template=intent_template,
                conversation_history=conversation_history,
                model_capabilities=capabilities,
            )
        except Exception as error:
            # Keep diagnostic locations, never the prompt, reference data or
            # arbitrary exception text that could contain private material.
            logger.error("Music agent context construction failed error=%s frames=%s",
                         type(error).__name__, [(f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)[-6:]])
            self._abort_collaboration(context)
            return self._outcome("context_error", (), context, ())
        if not capabilities.supports_streaming:
            logger.warning(
                "Music agent model capability is unavailable model=%s streaming=%s tools=%s",
                self._model_id,
                capabilities.supports_streaming,
                capabilities.supports_tools,
            )
            return self._terminal_outcome("model_unavailable", (), context, ())
        from ai_piano_studio.music_agent.resource_tools import STUDIO_ASSIST_TEMPLATE, STUDIO_TASK_TOOLS
        model_driven = intent_template == STUDIO_ASSIST_TEMPLATE
        tools: Sequence[Mapping[str, object]] = (
            self._registry.openai_schemas(read_only=intent == "READ_MUSIC")
            if intent != "CHAT" and capabilities.supports_tools
            else ()
        )
        if intent == 'CHAT' and capabilities.supports_tools:
            # Natural requests need not pass a keyword classifier to discover a
            # source or prepare a reversible separate draft. Project write tools
            # remain absent from chat and keep their existing permission checks.
            tools = [self._registry.lookup(name).openai_schema() for name in
                     ('inspect_music_resource', 'read_resource_notes', 'prepare_song_preview', 'propose_song_source',
                      'get_ui_style','apply_ui_style','restore_ui_style')
                     if name in self._registry.names]
            from ai_piano_studio.music_agent.tool_contracts import MusicPermission
            if MusicPermission.WRITE_NOTES in context.permissions:
                tools = self._registry.openai_schemas()
        elif (intent == 'READ_MUSIC' and capabilities.supports_tools
              and 'prepare_song_preview' in self._registry.names):
            # Drafts are separate from the authorization-scoped editor. A local
            # READ label must not suppress a user-requested source audition.
            tools = [*tools, self._registry.lookup('prepare_song_preview').openai_schema()]
        if intent=='READ_MUSIC' and capabilities.supports_tools:
            tools = [*tools, *(self._registry.lookup(n).openai_schema() for n in ('apply_ui_style','restore_ui_style')
                              if n in self._registry.names)]

        if intent_template == 'source_discovery':
            tools = [tool for tool in tools if self._registry.lookup(tool['function']['name']).read_only]
        if not model_driven:
            tools = [tool for tool in tools if tool['function']['name'] not in STUDIO_TASK_TOOLS
                     or intent_template in {'autonomous_reconstruction', 'autonomous_original', 'autonomous_creative'}]
        provider_calls = 0
        validation_model_repairs = 0
        local_repairs = 0

        def debug_snapshot() -> Mapping[str, object]:
            return self._build_debug_snapshot(
                context,
                intent=intent,
                model_capabilities=capabilities,
                app_capabilities=app_capabilities,
                tools=tools,
                steps=debug_steps,
                provider_calls=provider_calls,
                validation_model_repairs=validation_model_repairs,
                local_repairs=local_repairs,
            )

        text_parts: list[str] = []
        results: list[MusicToolResult] = []
        seen_call_ids: set[str] = set()
        pending_validation_failure: MusicToolResult | None = None
        pending_repair_tool_name: str | None = None
        pending_repair_arguments: Mapping[str, object] | None = None
        seen_validation_fingerprints = set()
        last_resource_failure, repeated_resource_failures = None, 0
        successful_write_calls = 0
        successful_note_additions = 0
        visual_score_seen = False
        source_tempo_confirmed = False
        source_tempo_repair = False
        source_tempo_repair_rounds = 0

        def reset_native_context() -> None:
            reset = getattr(self._provider, 'reset_for_supplement', None)
            if callable(reset) and reset():
                invalidate = getattr(self._gateway, 'invalidate_score_image_delivery', None)
                if callable(invalidate):
                    invalidate()
                for index, result in enumerate(results):
                    if result.data.get('images_delivered') is True:
                        results[index] = replace(result, data={**result.data, 'images_delivered': False})

        def inject_supplements() -> bool:
            nonlocal source_tempo_repair, source_tempo_repair_rounds
            pending = self._take_pending_messages()
            if not pending:
                return False
            # A real user supplement supersedes the internal correction's
            # write restriction; existing authorization still bounds all tools.
            source_tempo_repair = False
            source_tempo_repair_rounds = 0
            reset_native_context()
            messages.append({"role": "user", "content": (
                "以下是用户在本任务进行中发送的最新补充，后发要求优先。请接着处理，"
                "当前工程、工具权限、授权小节及执行模式不变；补充不扩大授权。\n\n"
                + "\n\n".join(pending))})
            return True

        while True:
            if self._is_cancelled():
                return self._terminal_outcome(
                    "cancelled", text_parts, context, results, debug_snapshot=debug_snapshot()
                )
            inject_supplements()
            if getattr(self._provider, 'native_context_reset_required', False) is True:
                reset_native_context()
            if source_tempo_repair and not source_tempo_confirmed:
                if source_tempo_repair_rounds >= MAX_SOURCE_TEMPO_REPAIR_ROUNDS:
                    return self._terminal_outcome(
                        'source_tempo_unconfirmed', text_parts, context, results,
                        debug_snapshot=debug_snapshot())
                source_tempo_repair_rounds += 1
            if (
                context.budget.limits.max_steps is not None
                and provider_calls >= context.budget.limits.max_steps
            ):
                return self._terminal_outcome(
                    "limit_exceeded", text_parts, context, results, debug_snapshot=debug_snapshot()
                )
            provider_calls += 1
            # Keep only this response's text for the UI. Earlier tool-round
            # commentary remains in the transport transcript below.
            text_parts.clear()
            if self._on_response_started is not None:
                try:
                    self._on_response_started()
                except Exception:
                    logger.warning('Music agent response-start callback failed')
            step_debug: dict[str, object] = {
                "stepIndex": provider_calls,
                "contextCoverage": dict(getattr(self._context_builder, "coverage", {})),
                "toolsSent": self._schema_names(tools),
                "toolCallsReturned": (),
                "toolResults": (),
            }
            try:
                self._progress(ProviderProgressEvent("connecting"))
                events = self._provider.stream_events(messages, tools, reasoning, attachments)
                event_list = self._read_events(events)
            except ProviderError as error:
                debug_steps.append(step_debug)
                logger.warning("Music provider returned a safe ProviderError")
                return self._terminal_outcome(
                    error.code,
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )
            except Exception as error:
                debug_steps.append(step_debug)
                normalizer = getattr(self._provider, "normalize_error", None)
                if callable(normalizer):
                    try:
                        normalizer(error)
                    except Exception:
                        logger.warning("Music provider error normalization failed")
                logger.warning("Music provider stream failed")
                return self._terminal_outcome(
                    "provider_error",
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )

            if event_list is None:
                debug_steps.append(step_debug)
                return self._terminal_outcome(
                    "cancelled",
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )
            if inject_supplements():
                # This response was produced before the new instructions. No
                # stale tool calls are executed; the next request includes the new input.
                step_debug["supersededBySupplement"] = True
                debug_steps.append(step_debug)
                continue
            calls: list[ProviderToolCall] = []
            for event in event_list:
                if event.text_delta:
                    text_parts.append(event.text_delta)
                calls.extend(event.tool_calls)
            step_debug["toolCallsReturned"] = tuple(call.name for call in calls)
            if not calls:
                debug_steps.append(step_debug)
                if pending_validation_failure is not None:
                    return self._terminal_outcome(
                        pending_validation_failure.error_code or "validation_error",
                        text_parts,
                        context,
                        results,
                        debug_snapshot=debug_snapshot(),
                    )
                if model_driven and any(r.success and r.tool_name == 'set_song_task' for r in results):
                    return self._terminal_outcome('source_unavailable', text_parts, context, results,
                                                  debug_snapshot=debug_snapshot())
                if successful_write_calls == 0:
                    if any(r.success and r.tool_name in {'apply_ui_style','restore_ui_style'} for r in results):
                        return self._terminal_outcome('finished', text_parts, context, results, debug_snapshot=debug_snapshot())
                    from ai_piano_studio.music_agent.song_resolver import requests_named_song
                    missing_source = (not model_driven
                        and (intent in {'CREATE_MUSIC','EDIT_MUSIC'} or requests_named_song(instruction))
                        and any(r.tool_name in {'inspect_music_resource','read_resource_notes'} for r in results))
                    if missing_source:
                        return self._terminal_outcome('source_unavailable', text_parts, context, results,
                                                      debug_snapshot=debug_snapshot())
                if (successful_write_calls == 0 and self._requests_missing_input(''.join(text_parts))
                        and intent_template not in {"autonomous_original", "autonomous_creative", "autonomous_reconstruction"}):
                    return self._terminal_outcome(
                        'needs_input', text_parts, context, results, debug_snapshot=debug_snapshot()
                    )
                if intent_template == 'source_discovery':
                    return self._terminal_outcome('needs_input', text_parts, context, results,
                                                  debug_snapshot=debug_snapshot())
                creation_needs_notes = intent == "CREATE_MUSIC"
                required_write_completed = (
                    successful_note_additions > 0
                    if creation_needs_notes
                    else successful_write_calls > 0
                )
                if (
                    intent in {"CREATE_MUSIC", "EDIT_MUSIC"}
                    and not required_write_completed
                    and not any(not result.success for result in results)
                ):
                    return self._terminal_outcome(
                        "no_write",
                        text_parts,
                        context,
                        results,
                        debug_snapshot=debug_snapshot(),
                    )
                if (intent == 'CREATE_MUSIC' and intent_template == 'autonomous_reconstruction'
                        and visual_score_seen and successful_note_additions > 0
                        and not source_tempo_confirmed):
                    if source_tempo_repair:
                        return self._terminal_outcome(
                            'source_tempo_unconfirmed', text_parts, context, results,
                            debug_snapshot=debug_snapshot())
                    source_tempo_repair = True
                    messages.append({'role': 'user', 'content': _SOURCE_TEMPO_ENFORCEMENT_MESSAGE})
                    continue
                if successful_write_calls > 0:
                    if context.mode is MusicExecutionMode.COLLABORATION:
                        completion = self._gateway.finish_turn(context)
                        if completion.success:
                            return self._outcome(
                                "finished",
                                text_parts,
                                context,
                                results,
                                debug_snapshot=debug_snapshot(),
                            )
                        return self._terminal_outcome(
                            "execution_error",
                            text_parts,
                            context,
                            results,
                            debug_snapshot=debug_snapshot(),
                        )
                    return self._outcome(
                        "proposal",
                        text_parts,
                        context,
                        results,
                        proposal=self._gateway.current_proposal(context),
                        debug_snapshot=debug_snapshot(),
                    )
                return self._terminal_outcome(
                    "finished",
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )
            if not capabilities.supports_tools:
                return self._terminal_outcome(
                    "tools_unavailable",
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )

            terminal_status: str | None = None
            turn_feedback: list[tuple[str, MusicToolResult]] = []
            completed_calls: list[ProviderToolCall] = []
            step_results: list[dict[str, object]] = []
            for call in calls:
                if self._is_cancelled():
                    step_debug["toolResults"] = tuple(step_results)
                    debug_steps.append(step_debug)
                    return self._terminal_outcome(
                        "cancelled",
                        text_parts,
                        context,
                        results,
                        debug_snapshot=debug_snapshot(),
                    )
                definition = self._registry.lookup(call.name) if call.name in self._schema_names(tools) else None
                if intent_template == 'source_discovery' and (definition is None or not definition.read_only):
                    return self._terminal_outcome('source_confirmation_required', text_parts, context, results,
                                                  debug_snapshot=debug_snapshot())
                if (source_tempo_repair and definition is not None and not definition.read_only
                        and call.name not in {'set_bpm', 'apply_ui_style', 'restore_ui_style'}):
                    # A metadata correction cannot silently replay the note write.
                    terminal_status = 'source_tempo_unconfirmed'
                    break
                if call.name in {"add_notes", "repeat_section"}:
                    self._progress(ProviderProgressEvent("generating"))
                self._progress(ProviderProgressEvent("reading" if definition is not None and definition.read_only else "writing"))
                result, local_change_count, _effective_arguments = self._execute_call(
                    call,
                    context,
                    seen_call_ids,
                    pending_tool_name=pending_repair_tool_name,
                    pending_arguments=pending_repair_arguments,
                    pending_failure=pending_validation_failure,
                )
                local_repairs += local_change_count
                images = result.data.get('_provider_images', ())
                if result.success and images:
                    if getattr(self._provider, 'supports_tool_images', False) is True:
                        try:
                            self._provider.set_tool_result_images(call.call_id, images)
                            result = replace(result, data={**result.data, 'images_delivered': True})
                            acknowledge = getattr(self._gateway, 'acknowledge_score_images', None)
                            if callable(acknowledge):
                                acknowledge(result.data.get('source_sha256'))
                        except ProviderError as error:
                            return self._terminal_outcome(error.code, text_parts, context, results,
                                                          debug_snapshot=debug_snapshot())
                    else:
                        result = MusicToolResult.failure(call.name, 'resource_unavailable',
                            'Visual reference is not readable on this connection.',
                            data={**{k: v for k, v in result.data.items() if not k.startswith('_')},
                                  'status': 'unsupported', 'reason': 'This connection cannot receive score images.',
                                  'next_action': 'Try a parsed MIDI/MusicXML source; do not invent notes from unread images.'})
                results.append(result)
                if result.success and result.data.get('status') == 'visual_score' and result.data.get('images_delivered') is True:
                    visual_score_seen = True
                if result.success and call.name == 'set_bpm':
                    source_tempo_confirmed = True
                if result.success and call.name in {'start_original_draft', 'start_song_draft'}:
                    return self._outcome(call.name, text_parts, context, results,
                                         debug_snapshot=debug_snapshot())
                if (call.name == 'propose_song_source' and result.success
                        and result.data.get('status') == 'source_confirmation'):
                    self._abort_collaboration(context)
                    return self._outcome('source_confirmation', text_parts, context, results,
                                         debug_snapshot=debug_snapshot())
                if (call.name == 'prepare_song_preview' and result.success
                        and result.data.get('status') == 'preview_ready'):
                    # A separate draft is real work; do not coerce a destructive
                    # current-project write after it has been prepared.
                    self._abort_collaboration(context)
                    return self._outcome('resource_preview', text_parts, context, results,
                                         debug_snapshot=debug_snapshot())
                step_results.append(
                    {
                        "tool": result.tool_name,
                        "success": result.success,
                        "errorCode": result.error_code,
                    }
                )
                completed_calls.append(call)
                turn_feedback.append((call.call_id, result))
                if result.error_code == "limit_exceeded":
                    terminal_status = "limit_exceeded"
                    break
                if not result.success:
                    if result.error_code == 'resource_unavailable':
                        # Network/format failures are real failed reads, but a new
                        # source, viewport or argument can still complete the task.
                        fingerprint = json.dumps([call.name, _effective_arguments, result.data.get('reason')],
                                                 sort_keys=True, default=str)
                        repeated_resource_failures = repeated_resource_failures + 1 if fingerprint == last_resource_failure else 1
                        last_resource_failure = fingerprint
                        if repeated_resource_failures >= 3:
                            terminal_status = 'resource_unavailable'
                            break
                        continue
                    error_class = classify_tool_result(result)
                    if error_class is ToolErrorClass.MODEL_REPAIRABLE:
                        fingerprints = frozenset((json.dumps([call.name, _effective_arguments,
                            sorted(map(repr, validation_fingerprints(result)))], sort_keys=True, default=str),))
                        repeated = any(
                            fingerprint in seen_validation_fingerprints
                            for fingerprint in fingerprints
                        )
                        if repeated:
                            terminal_status = result.error_code or "validation_error"
                            break
                        seen_validation_fingerprints.update(fingerprints)
                        validation_model_repairs += 1
                        pending_validation_failure = result
                        pending_repair_tool_name = call.name
                        pending_repair_arguments = _effective_arguments
                        if self._on_validation_repair is not None:
                            invalid_count = result.data.get("invalid_items")
                            count = len(invalid_count) if isinstance(invalid_count, list) else 1
                            try:
                                self._on_validation_repair(count)
                            except Exception:
                                logger.warning("Music agent validation repair callback failed")
                        break
                    terminal_status = result.error_code or "execution_error"
                    break
                last_resource_failure, repeated_resource_failures = None, 0
                if (
                    pending_repair_tool_name is None
                    or call.name == pending_repair_tool_name
                ):
                    pending_validation_failure = None
                    pending_repair_tool_name = None
                    pending_repair_arguments = None
                if (result.success and self._is_write(call.name)
                        and call.name not in {'apply_ui_style','restore_ui_style'}):
                    successful_write_calls += 1
                    successful_note_additions += result.added_notes
                    continue
            step_debug["toolResults"] = tuple(step_results)
            debug_steps.append(step_debug)
            transcript = self._tool_transcript_messages(completed_calls, turn_feedback)
            # Transport-only continuation state; never add it to UI text or diagnostics.
            reasoning_parts = [event.reasoning_content for event in event_list if event.reasoning_content is not None]
            if reasoning_parts:
                transcript[0]["reasoning_content"] = "".join(reasoning_parts)
            assistant_text = "".join(event.text_delta for event in event_list)
            if assistant_text:
                transcript[0]["content"] = assistant_text
            messages.extend(transcript)
            if terminal_status is not None:
                return self._terminal_outcome(
                    terminal_status,
                    text_parts,
                    context,
                    results,
                    debug_snapshot=debug_snapshot(),
                )

    @staticmethod
    def _requests_missing_input(text: str) -> bool:
        if (re.search(r"(?:未找到|没有|无法获得|无法取得).{0,30}(?:后续曲谱|后续谱面|后续.{0,5}MIDI)", text, re.I)
                and re.search(r"暂不写入|不写入|保留现有|尚未完成", text)):
            return True
        return bool(re.search(
            r'(?:请(?:先)?(?:提供|上传|导入|选择|告诉我)|需要你提供).{0,60}'
            r'(?:简谱|五线谱|曲谱|谱面|音频|midi|文件|小节|范围|风格|用途|版本|歌手|曲名|修改重点)'
            r'|please\s+(?:provide|upload|select).{0,60}(?:score|sheet music|audio|midi|file|bars|range)',
            text, re.IGNORECASE,
        )) or bool(re.search(
            r'(?:你指的是|请确认|想先确认).{0,60}(?:哪首|哪个版本|歌手|曲名|哪一段|哪段|哪个声部)'
            r'|(?:旋律|节奏|伴奏|和声).{0,30}还是.{0,30}[？?]'
            r'|(?:你想|你希望|你更偏向|想先确认).{0,30}(?:哪种风格|什么风格|哪种感觉|什么用途).{0,30}[？?]'
            r'|which\s+(?:song|version|section|track|style)\b.{0,60}\?',
            text, re.IGNORECASE,
        ))

    def _capabilities(self) -> ModelCapabilities:
        capabilities = self._provider.get_capabilities(self._model_id)
        if not isinstance(capabilities, ModelCapabilities):
            raise ProviderError("provider capabilities are invalid")
        return capabilities

    def _read_events(self, events: object) -> list[ProviderEvent] | None:
        collected: list[ProviderEvent] = []
        first = True
        for event in events:
            if self._is_cancelled():
                return None
            if not isinstance(event, ProviderEvent):
                raise ProviderError("provider returned an invalid event")
            if first:
                self._progress(ProviderProgressEvent("responding", response_received=True))
                first = False
            if event.text_delta and self._on_text_delta is not None:
                try:
                    self._on_text_delta(event.text_delta)
                except Exception:
                    logger.warning("Music agent text update callback failed")
            collected.append(event)
        return collected

    def _execute_call(
        self,
        call: ProviderToolCall,
        context: MusicAgentExecutionContext,
        seen_call_ids: set[str],
        *,
        pending_tool_name: str | None = None,
        pending_arguments: Mapping[str, object] | None = None,
        pending_failure: MusicToolResult | None = None,
    ) -> tuple[MusicToolResult, int, Mapping[str, object] | None]:
        if call.call_id in seen_call_ids:
            return MusicToolResult.failure(
                "unregistered", "validation_error", "duplicate tool call"
            ), 0, None
        seen_call_ids.add(call.call_id)
        if call.name not in self._registry.names:
            return MusicToolResult.failure(
                "unregistered", "unknown_tool", "tool is not registered"
            ), 0, None
        try:
            arguments = json.loads(call.arguments_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Provider returned malformed JSON tool arguments")
            return MusicToolResult.failure(
                call.name, "validation_error", "tool arguments must be a JSON object"
            ), 0, None
        if not isinstance(arguments, dict):
            return MusicToolResult.failure(
                call.name, "validation_error", "tool arguments must be a JSON object"
            ), 0, None
        bar_duration_seconds: float | None = None
        duration_reader = getattr(self._gateway, "current_bar_duration_seconds", None)
        if callable(duration_reader):
            try:
                bar_duration_seconds = duration_reader(context)
            except Exception:
                bar_duration_seconds = None
        normalized = self._argument_normalizer.normalize(
            call.name,
            arguments,
            authorized_bars=context.authorized_bars,
            bar_duration_seconds=bar_duration_seconds,
        )
        effective_arguments: Mapping[str, object] = normalized.arguments
        if (
            pending_tool_name == call.name
            and pending_arguments is not None
            and pending_failure is not None
        ):
            effective_arguments = self._merge_repair_arguments(
                pending_arguments,
                normalized.arguments,
                pending_failure,
            )
        return (
            self._gateway.execute(call.name, effective_arguments, context),
            len(normalized.changes),
            effective_arguments,
        )

    @staticmethod
    def _merge_repair_arguments(
        original: Mapping[str, object],
        corrected: Mapping[str, object],
        failure: MusicToolResult,
    ) -> Mapping[str, object]:
        """Merge a partial model correction back into the failed item list."""

        raw_items = failure.data.get("invalid_items")
        if not isinstance(raw_items, list):
            return corrected
        indexes = [
            item.get("item_index")
            for item in raw_items
            if isinstance(item, Mapping)
            and isinstance(item.get("item_index"), int)
            and not isinstance(item.get("item_index"), bool)
        ]
        if not indexes:
            return corrected
        merged = dict(corrected)
        for key in ("notes", "updates"):
            original_items = original.get(key)
            corrected_items = corrected.get(key)
            if not isinstance(original_items, list) or not isinstance(corrected_items, list):
                continue
            if len(corrected_items) != len(indexes) or len(corrected_items) == len(original_items):
                continue
            if any(index < 0 or index >= len(original_items) for index in indexes):
                continue
            combined = list(original_items)
            for index, item in zip(indexes, corrected_items, strict=True):
                combined[index] = item
            merged[key] = combined
            return merged
        return corrected

    def _is_write(self, tool_name: str) -> bool:
        return not self._registry.lookup(tool_name).read_only

    @staticmethod
    def _assistant_tool_calls_message(calls: Sequence[ProviderToolCall]) -> dict[str, object]:
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                    **({"extra_content": {"google": {"thought_signature": call.thought_signature}}}
                       if call.thought_signature is not None else {}),
                }
                for call in calls
            ],
        }

    @staticmethod
    def _tool_feedback_messages(
        feedback: Sequence[tuple[str, MusicToolResult]],
    ) -> list[dict[str, object]]:
        return [
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": MusicAgentLoop._tool_feedback_content(result),
            }
            for call_id, result in feedback
        ]

    @staticmethod
    def _tool_transcript_messages(
        calls: Sequence[ProviderToolCall],
        feedback: Sequence[tuple[str, MusicToolResult]],
    ) -> list[dict[str, object]]:
        """Return one assistant Tool turn followed by its matching bounded feedback."""
        for call, (feedback_call_id, _result) in zip(calls, feedback, strict=True):
            if call.call_id != feedback_call_id:
                raise ValueError("tool transcript feedback must match its provider call")
        return [
            MusicAgentLoop._assistant_tool_calls_message(calls),
            *MusicAgentLoop._tool_feedback_messages(feedback),
        ]

    @staticmethod
    def _tool_feedback_content(result: MusicToolResult) -> str:
        return serialize_provider_feedback(result)

    @staticmethod
    def _feedback_data(data: Mapping[str, object]) -> dict[str, object]:
        """Compatibility projection for non-tool-specific callers."""
        result = MusicToolResult.success_result("generic", "", data=data)
        return json.loads(serialize_provider_feedback(result))["data"]

    def _terminal_outcome(
        self,
        status: str,
        text_parts: Sequence[str],
        context: MusicAgentExecutionContext,
        results: Sequence[MusicToolResult],
        *,
        debug_snapshot: Mapping[str, object] | None = None,
    ) -> MusicAgentLoopOutcome:
        if status != "finished":
            self._abort_collaboration(context)
            self._discard_proposal(context)
        elif context.mode is MusicExecutionMode.COLLABORATION:
            self._abort_collaboration(context)
        return self._outcome(
            status,
            text_parts,
            context,
            results,
            debug_snapshot=debug_snapshot,
        )

    def _abort_collaboration(self, context: MusicAgentExecutionContext) -> None:
        if context.mode is MusicExecutionMode.COLLABORATION:
            self._gateway.abort_turn(context)

    def _discard_proposal(self, context: MusicAgentExecutionContext) -> None:
        if context.mode is not MusicExecutionMode.PROPOSAL:
            return
        try:
            self._gateway.release_proposal(context)
        except Exception:
            logger.warning("Music proposal workspace cleanup failed")

    def _outcome(
        self,
        status: str,
        text_parts: Sequence[str],
        context: MusicAgentExecutionContext,
        results: Sequence[MusicToolResult],
        *,
        proposal: MusicEditProposal | None = None,
        debug_snapshot: Mapping[str, object] | None = None,
    ) -> MusicAgentLoopOutcome:
        tool_results = tuple(results)
        successful = tuple(result for result in tool_results if result.success)
        write_count = sum(1 for result in successful if self._is_write(result.tool_name))
        read_count = len(successful) - write_count
        return MusicAgentLoopOutcome(
            status=status,
            text="".join(text_parts),
            context=context,
            proposal=proposal,
            tool_results=tool_results,
            tool_call_count=len(tool_results),
            read_count=read_count,
            write_count=write_count,
            debug_snapshot=debug_snapshot or {},
        )

    @staticmethod
    def _schema_names(tools: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
        names: list[str] = []
        for schema in tools:
            function = schema.get("function")
            if isinstance(function, Mapping):
                name = function.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
        return tuple(names)

    @classmethod
    def _build_debug_snapshot(
        cls,
        context: MusicAgentExecutionContext,
        *,
        intent: str,
        model_capabilities: ModelCapabilities,
        app_capabilities: AssistantCapabilitySet,
        tools: Sequence[Mapping[str, object]],
        steps: Sequence[Mapping[str, object]],
        provider_calls: int = 0,
        validation_model_repairs: int = 0,
        local_repairs: int = 0,
    ) -> Mapping[str, object]:
        return {
            "intent": intent,
            "modelToolSupport": model_capabilities.supports_tools,
            "appCapabilities": app_capabilities.to_dict(),
            "turnPermissions": tuple(sorted(permission.value for permission in context.permissions)),
            "effectiveToolNames": cls._schema_names(tools),
            "mode": context.mode.value,
            "authorizedRanges": tuple(
                {
                    "startBar": item.start_bar,
                    "endBar": item.end_bar,
                }
                for item in context.authorized_bars
            ),
            "steps": tuple(dict(step) for step in steps),
            "provider_calls": provider_calls,
            "validation_model_repairs": validation_model_repairs,
            "local_repairs": local_repairs,
        }
