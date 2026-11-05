"""A general conversation/tool loop; the composer chooses its own workflow."""
from __future__ import annotations

import base64
import json

from ai_piano_studio.music_agent.creator_tools import DEFAULT_CREATOR_INSTRUCTIONS
from ai_piano_studio.music_agent.creator_recovery import RecoveryReview
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from ai_piano_studio.music_agent.providers import ProviderError
from ai_piano_studio.music_agent.tool_contracts import MusicExecutionMode, MusicToolResult


def history_with_character_limit(history, limit):
    """User-selected history budget in Unicode characters; zero retains all history."""
    messages = [dict(role=m['role'], content=m['content']) for m in history
                if m.get('role') in {'user', 'assistant'} and isinstance(m.get('content'), str)]
    if limit <= 0:
        return messages
    selected = []
    remaining = limit
    for message in reversed(messages):
        if remaining <= 0:
            break
        content = message['content'][-remaining:]
        selected.append(dict(role=message['role'], content=content))
        remaining -= len(content)
    return list(reversed(selected))


def run_creator(loop, instruction, context, *, reasoning, attachments, conversation_history):
    gateway = loop._gateway
    facts = gateway.creator_facts(context)
    prompt = facts.pop('instructions', DEFAULT_CREATOR_INSTRUCTIONS)
    recovery_review = facts.pop('recovery_review', True)
    history_limit = facts.pop('history_character_limit', 0)
    messages = [dict(role='system', content=prompt),
                *history_with_character_limit(conversation_history, history_limit),
                dict(role='user', content=instruction + '\n\n当前应用状态：\n' + json.dumps(facts, ensure_ascii=False, default=str))]
    # These old functions change orchestration rather than provide a capability.
    # The general loop instead exposes file previews and live app access.
    old_routes = {'set_song_task', 'start_song_draft', 'start_original_draft', 'propose_song_source'}
    tools = [loop._registry.lookup(name).openai_schema() for name in loop._registry.names if name not in old_routes]
    results, steps, text, seen = [], [], '', {}
    review = RecoveryReview(recovery_review)
    call_signatures = {}
    provider_calls = 0

    def reset_session():
        reset = getattr(loop._provider, 'reset_for_supplement', None)
        if callable(reset):
            if reset():
                seen.clear()
                call_signatures.clear()
            gateway.invalidate_score_image_delivery()

    def outcome(status):
        effective = gateway.creator_execution_context(context)
        receipt = (MusicToolResult.success_result('__turn__', 'Turn ended.', data=dict(status=status))
                   if status in {'finished', 'resource_preview', 'proposal'} else
                   MusicToolResult.failure('__turn__', 'execution_error', 'Turn ended without a completed result.', data=dict(status=status)))
        gateway.creator_record_receipt('__turn__', receipt)
        return loop._outcome(status, [text], effective, results, debug_snapshot=dict(steps=steps, creator=True,
            provider_calls=provider_calls, initial_revision=context.baseline_revision, initial_project_identity=context.project_identity))

    try:
        while not loop._is_cancelled():
            pending = loop._take_pending_messages()
            if pending:
                reset_session()
                messages.extend(dict(role='user', content=value) for value in pending)
            if loop._on_response_started is not None:
                loop._on_response_started()
            loop._progress(ProviderProgressEvent('connecting'))
            try:
                provider_calls += 1
                events = loop._read_events(loop._provider.stream_events(messages, tools, reasoning, attachments))
            except ProviderError as error:
                text = str(error)
                return outcome(error.code)
            if events is None:
                return outcome('cancelled')
            text = ''.join(event.text_delta for event in events)
            calls = [call for event in events for call in event.tool_calls]
            if not calls:
                pending = loop._take_pending_messages()
                if pending:
                    # Normally supplements arrive through the loop's event hook;
                    # do not finish while the controller has a queued amendment.
                    messages.extend(dict(role='user', content=value) for value in pending)
                    reset_session()
                    continue
                pending_commands = gateway.creator_pending_commands()
                feedback = review.feedback(pending_commands) if not gateway.creator_has_preview else None
                if feedback:
                    gateway.creator_record_receipt('__review__', MusicToolResult.success_result('__review__', 'Recovery review started.'))
                    messages.append(dict(role='assistant', content=text))
                    messages.append(dict(role='developer', content=feedback))
                    reset_session()
                    loop._progress(ProviderProgressEvent('verifying'))
                    continue
                if pending_commands:
                    return outcome('unfinished_commands')
                if gateway.creator_has_preview:
                    return outcome('resource_preview')
                effective = gateway.creator_execution_context(context)
                if effective.mode is MusicExecutionMode.PROPOSAL:
                    proposal = gateway.current_proposal(effective)
                    if proposal is not None and proposal.operations:
                        return outcome('proposal')
                return outcome('finished')
            assistant_message = loop._assistant_tool_calls_message(calls)
            if text:
                assistant_message['content'] = text
            reasoning_parts = [event.reasoning_content for event in events if event.reasoning_content is not None]
            if reasoning_parts:
                assistant_message['reasoning_content'] = ''.join(reasoning_parts)
            messages.append(assistant_message)
            images = []
            for call in calls:
                if loop._is_cancelled():
                    return outcome('cancelled')
                payload = {}
                try:
                    payload = json.loads(call.arguments_json)
                    if not isinstance(payload, dict):
                        raise ValueError('Tool arguments must be a JSON object')
                    signature = review.signature(call.name, payload)
                    if call.call_id in seen and call_signatures[call.call_id] != signature:
                        raise ValueError('The same tool call ID was reused with different arguments; send a new call ID.')
                    if call.call_id in seen:
                        # A repeated transport ID reuses the receipt, not its side effect.
                        result = seen[call.call_id]
                    else:
                        result = gateway.execute(call.name, payload, gateway.creator_execution_context(context))
                        seen[call.call_id] = result
                        call_signatures[call.call_id] = signature
                except Exception as error:
                    if loop._is_cancelled():
                        return outcome('cancelled')
                    result = MusicToolResult.failure(call.name,
                        'validation_error' if isinstance(error, (ValueError, TypeError, KeyError)) else 'execution_error',
                        str(error))
                media = result.data.get('_provider_images', ())
                if media:
                    image_setter = getattr(loop._provider, 'set_tool_result_images', None)
                    if callable(image_setter):
                        try:
                            image_setter(call.call_id, media)
                        except (ProviderError, ValueError, TypeError) as error:
                            result = MusicToolResult.failure(call.name, 'execution_error', str(error), data=dict(reason='image_delivery_error'))
                    else:
                        images.extend(dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(raw).decode())) for raw in media)
                public = {key: value for key, value in result.data.items() if key != '_provider_images'}
                review.observe(call.name, payload, result)
                gateway.creator_record_receipt(call.name, result)
                results.append(result)
                messages.append(dict(role='tool', tool_call_id=call.call_id, content=json.dumps(dict(
                    success=result.success, tool=result.tool_name, summary=result.summary, data=public,
                    error_code=result.error_code, warnings=result.warnings), ensure_ascii=False, default=str)))
                steps.append(dict(tool=call.name, success=result.success, errorCode=result.error_code))
            if images:
                messages.append(dict(role='user', content=[dict(type='text', text='工具返回的图片'), *images]))
            attachments = ()
        return outcome('cancelled')
    finally:
        gateway.creator_finish_turn(context.turn_id)
