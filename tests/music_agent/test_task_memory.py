import json
import sqlite3

from ai_piano_studio.music_agent.task_memory import TaskMemory
from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from ai_piano_studio.music_agent.providers import ProviderEvent
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from tests.music_agent.test_conversation_history import controller
from tests.music_agent.test_creation_workflow import assistant_for, done, music_scripts, accept_prepared


def test_latest_long_chinese_correction_survives_next_turn_budget():
    correction = "不要循环开头" + "中" * 17_000 + "改成25秒"
    history = [{"role": "user", "content": "开始创作10秒"},
               {"role": "assistant", "content": "早先回复"},
               {"role": "user", "content": correction}]
    selected = MusicAgentContextBuilder._conversation_history(history)
    rendered = json.dumps(selected, ensure_ascii=False)
    assert "不要循环开头" in rendered and "改成25秒" in rendered
    assert sum(len(m["content"].encode()) for m in selected) <= 48_000
    assert "历史已缩短" in rendered


def test_recent_turn_keeps_its_user_and_response_together():
    history = [{"role": "user", "content": "原要求"}]
    for i in range(20):
        history += [{"role": "user", "content": f"纠正{i}"},
                    {"role": "assistant", "content": "答" * 1500}]
    selected = MusicAgentContextBuilder._conversation_history(history)
    assert selected[-2]["content"] == "纠正19"
    assert selected[-1]["role"] == "assistant"
    assert 12 < len(selected) <= 32


def test_task_persists_corrections_facts_and_isolates_conversations(tmp_path):
    path = tmp_path / "memory.sqlite3"
    m = TaskMemory(path, "a")
    m.accept("按原曲续写到10秒，可以循环", "EDIT_MUSIC")
    m.accept("不要重复，扩展到25秒，midi自己找", "EDIT_MUSIC")
    m.record("failed", {"errorCode": "no_write", "fingerprint": "old"})
    m.accept("hi", "CHAT")
    restored = TaskMemory(path, "a")
    assert restored.state["constraints"] == {"targetSeconds": 25.0, "allowRepeat": False,
                                            "sourceMode": "original_requested_not_verified"}
    assert restored.state["status"] == "failed"
    assert restored.context({"fingerprint": "new"})["receiptIsHistorical"]
    assert not TaskMemory(path, "b").state
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM assistant_task_events").fetchone()[0] == 3
    restored.clear()
    assert not TaskMemory(path, "a").state


def test_latest_constraint_can_replace_prohibition_and_source_mode(tmp_path):
    m = TaskMemory(tmp_path / "m.db", "a")
    m.accept("原曲，不要重复，25秒", "CREATE_MUSIC")
    m.accept("改为原创，可以循环，30秒", "EDIT_MUSIC")
    assert m.state["constraints"] == {"targetSeconds": 30, "allowRepeat": True,
                                     "sourceMode": "original_composition"}
    task_id = m.state["taskId"]
    m.record("completed", {"committed": True}, task_id="old-response")
    assert m.state["status"] == "pending"
    assert m.state["taskId"] == task_id


def test_controller_restart_restores_goal_but_never_old_authority(tmp_path, qapp):
    path = tmp_path / "history.db"
    first = controller(path)
    first._memory_accept("按原曲续写到25秒，不要循环", "EDIT_MUSIC")
    first._memory_record("failed", {"errorCode": "no_write"})
    second = controller(path)
    assert second._task_memory.state["constraints"]["targetSeconds"] == 25
    assert second._last_music_permissions == frozenset()
    assert second._last_music_intent == ""
    assert second._resolve_turn_intent("hi") == "CHAT"
    second.clearConversation()
    assert not controller(path)._task_memory.state


def test_autonomous_creation_receives_history_and_adopted_task(tmp_path, qapp):
    assistant, playback, provider = assistant_for(tmp_path, EditorController(ProjectState()), music_scripts())
    assistant._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, "保持安静，不要密集伴奏"))
    assert assistant.sendMessage("创作一首30秒忧伤钢琴曲")
    done(qapp, assistant)
    request = json.dumps(provider.requests[0], ensure_ascii=False)
    assert "保持安静，不要密集伴奏" in request
    assert "应用维护的当前任务记录" in request
    assert assistant._task_memory.state["status"] == "proposal"
    accept_prepared(assistant)
    assert assistant._task_memory.state["status"] == "completed"
    assert assistant._task_memory.state["receipt"]["committed"]


def test_progress_complaint_after_hi_reads_existing_goal_without_starting_creation(tmp_path, qapp):
    scripts = [(ProviderEvent(text_delta="Hi!", finish_reason="stop"),),
               (ProviderEvent(text_delta="还没有写入，目标仍是25秒。", finish_reason="stop"),)]
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), scripts)
    assistant._memory_accept("按原曲往下接，写到25秒，midi文件自己找", "EDIT_MUSIC")
    assistant._memory_record("failed", {"errorCode": "no_write"})
    assert assistant.sendMessage("hi")
    done(qapp, assistant)
    assert assistant.sendMessage("刚刚我让你按原曲往下接，写到25秒，你怎么没动")
    done(qapp, assistant)
    request = json.dumps(provider.requests[-1], ensure_ascii=False)
    assert "25秒" in request and "midi文件自己找" in request
    assert assistant.lastTurnResult["intent"] == "READ_MUSIC"
    assert assistant._task_memory.state["status"] == "failed"


def test_target_duration_uses_destination_and_negated_original_is_not_permission():
    m = TaskMemory(None, "a")
    m.accept("把10秒片段续写到25秒，按原曲，不要原创旋律", "EDIT_MUSIC")
    assert m.state["constraints"]["targetSeconds"] == 25
    assert m.state["constraints"]["sourceMode"] == "original_requested_not_verified"


def test_new_composition_does_not_inherit_failed_song_constraints():
    m = TaskMemory(None, "a")
    m.accept("我要《青花瓷》原曲25秒，不要重复", "CREATE_MUSIC")
    old = m.state["taskId"]
    m.record("failed", {"errorCode": "no_write"})
    m.accept("另写一首原创舒缓钢琴曲", "CREATE_MUSIC", new_task=True)
    assert m.state["taskId"] != old
    assert m.state["goal"] == "另写一首原创舒缓钢琴曲"
    assert "targetSeconds" not in m.state["constraints"]
    assert "songTitle" not in m.state["constraints"]


def test_transient_database_failure_can_be_retried_without_poisoned_version(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import pytest
    m = TaskMemory(tmp_path / "m.db", "a")
    original = m._db
    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError("simulated lock")
        yield
    monkeypatch.setattr(m, "_db", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        m.accept("按原曲写到25秒", "EDIT_MUSIC")
    assert m.state["version"] == 0
    monkeypatch.setattr(m, "_db", original)
    m.record("pending", {"recovered": True})
    assert TaskMemory(m.path, "a").state["constraints"]["targetSeconds"] == 25


def test_stale_controller_never_overwrites_newer_task(tmp_path):
    import pytest
    path = tmp_path / "m.db"
    first, stale = TaskMemory(path, "a"), TaskMemory(path, "a")
    first.accept("25秒", "EDIT_MUSIC")
    with pytest.raises(sqlite3.OperationalError):
        stale.accept("10秒", "EDIT_MUSIC")
    assert TaskMemory(path, "a").state["constraints"]["targetSeconds"] == 25


def test_completed_receipt_reconciles_after_undo_and_restart(tmp_path):
    path = tmp_path / "m.db"
    m = TaskMemory(path, "a")
    m.accept("25秒", "EDIT_MUSIC")
    m.record("completed", {"fingerprint": "25s", "committed": True})
    restored = TaskMemory(path, "a")
    restored.reconcile({"fingerprint": "10s", "durationSeconds": 10})
    assert TaskMemory(path, "a").state["status"] == "needs_revalidation"


def test_old_untyped_corrections_can_be_retrieved_through_real_agent_tool(tmp_path, qapp):
    from ai_piano_studio.music_agent.providers import ProviderToolCall
    from ai_piano_studio.domain.note import Note
    from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider
    m = TaskMemory(None, "a")
    m.accept("创作C小调钢琴曲", "CREATE_MUSIC")
    m.accept("改为D大调", "EDIT_MUSIC")
    for i in range(16):
        m.accept(f"力度改为{50+i}", "EDIT_MUSIC")
    assert m.context({})["instructionsOmitted"]
    provider = _SerializedLoopProvider([
        (ProviderEvent(tool_calls=(ProviderToolCall("history", "get_task_history", json.dumps(
            {"query": "D大调", "offset": 0, "char_offset": 0})),)),),
        (ProviderEvent(text_delta="之前改为了D大调。", finish_reason="stop"),),
    ])
    editor = EditorController(ProjectState(notes=(Note.create(pitch=60, start_time=0, duration=1),)))
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant._task_memory = m
    assert assistant.sendMessage("读取当前旋律")
    done(qapp, assistant)
    assert len(provider.requests) == 2
    assert "改为D大调" in json.dumps(provider.requests[-1], ensure_ascii=False)
    assert len(editor.state.active_notes) == 1


def test_retry_keeps_named_song_and_target_in_source_preparation(tmp_path, qapp):
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), music_scripts())
    assistant._memory_accept("我要《青花瓷》原曲25秒，自己找MIDI", "CREATE_MUSIC")
    assistant._memory_record("failed", {"errorCode": "no_write"})
    task_id = assistant._task_memory.state["taskId"]
    looked_up = []
    class Resolver:
        def resolve(self, instruction, **kwargs):
            looked_up.append(instruction)
            return None
    assistant._creation_workflow.resolver = Resolver()
    assert assistant.sendMessage("重试")
    done(qapp, assistant)
    assert looked_up and "青花瓷" in looked_up[0] and "25秒" in looked_up[0]
    assert assistant._task_memory.state["taskId"] == task_id
    assert assistant.taskPreviewAvailable
    assert "青花瓷" in json.dumps(provider.requests[0], ensure_ascii=False)


def test_completed_task_and_selection_do_not_replay_writes(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider
    editor = EditorController(ProjectState(notes=(Note.create(id="a", pitch=60, start_time=0, duration=25),)))
    provider = _SerializedLoopProvider([(ProviderEvent(text_delta="已经完成25秒。", finish_reason="stop"),)])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assistant._memory_accept("续写到25秒", "EDIT_MUSIC")
    assistant._memory_record("completed", {"committed": True})
    editor.selectNote("a")
    assistant._memory_reconcile()
    assert assistant._task_memory.state["status"] == "completed"
    assert assistant.sendMessage("继续")
    done(qapp, assistant)
    assert assistant.lastTurnResult["intent"] == "READ_MUSIC"
    assert len(editor.state.active_notes) == 1
    assert assistant._task_memory.state["status"] == "completed"


def test_task_clear_failure_preserves_chat_for_retry(tmp_path, qapp, monkeypatch):
    assistant = controller(tmp_path / "history.db")
    assistant._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, "历史消息"))
    def fail():
        raise sqlite3.OperationalError("simulated lock")
    monkeypatch.setattr(assistant._task_memory, "clear", fail)
    assistant.clearConversation()
    assert assistant._messages.entries[0].text == "历史消息"
    assert "清除失败" in assistant.conversationError


def test_current_message_preserves_all_requirements_including_middle_and_tail():
    # Capability policy keeps the latest user message intact; only historical
    # context is compacted and recoverable through the history tool.
    from tests.music_agent.test_context_builder import _gateway
    from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
    gateway = _gateway()
    context = gateway.create_execution_context(authorized_bars=(BarRange(1, 1),),
        permissions=frozenset({MusicPermission.READ_PROJECT_SUMMARY}))
    builder = MusicAgentContextBuilder(gateway)
    instruction = "请按原曲" + "中" * 10000 + "中间要求：副歌" + "后" * 10000 + "最终纠正：不要循环"
    messages = builder.build(instruction, context)
    assert "最终纠正：不要循环" in messages[-1]["content"]
    assert instruction in messages[-1]['content']


def test_completion_uses_authoring_notes_before_playback_sync(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider
    old = Note.create(id="old", pitch=60, start_time=0, duration=10)
    new = Note.create(id="new", pitch=64, start_time=10, duration=15)
    editor = EditorController(ProjectState(notes=(old, new), playback_notes=(old,)))
    assistant = _serialized_loop_assistant(tmp_path, editor, _SerializedLoopProvider([]))
    assert assistant._memory_project_facts()["playbackDurationSeconds"] == 10
    assistant._memory_accept("续写到25秒", "EDIT_MUSIC")
    assistant._memory_record("completed", {"committed": True})
    assert assistant._task_memory.state["status"] == "completed"
    assert assistant._task_memory.state["receipt"]["durationSeconds"] == 25
    before = assistant._memory_project_facts()["fingerprint"]
    editor.synchronizeNow()
    assert assistant._memory_project_facts()["fingerprint"] == before
    assistant._memory_reconcile()
    assert assistant._task_memory.state["status"] == "completed"


def test_real_commit_has_clear_receipt_when_model_incorrectly_says_apply(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from ai_piano_studio.music_agent.providers import ProviderToolCall
    from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider
    editor = EditorController(ProjectState(notes=(Note.create(id="a", pitch=60, start_time=0, duration=1),)))
    provider = _SerializedLoopProvider([
        (ProviderEvent(tool_calls=(ProviderToolCall("write", "update_notes", '{"updates":[{"id":"a","pitch":64}]}'),)),),
        (ProviderEvent(text_delta="已暂存，可点击应用。", finish_reason="stop"),),
    ])
    assistant = _serialized_loop_assistant(tmp_path, editor, provider)
    assert assistant.sendMessage("把当前旋律升高4个半音，直接修改进去")
    done(qapp, assistant)
    assert assistant.lastTurnResult["committed"]
    assert editor.state.active_notes[0].pitch == 64
    assert any("无需再次点击应用" in m.text for m in assistant.messages.entries)


def test_progress_question_quoting_previous_edit_is_read_only_to_actual_end(tmp_path, qapp):
    from ai_piano_studio.domain.note import Note
    from tests.music_agent.test_assistant_controller import _serialized_loop_assistant, _SerializedLoopProvider
    from ai_piano_studio.music_agent.tool_contracts import MusicPermission, BarRange
    notes = tuple(Note.create(id=f"n{i}", pitch=60+i%5, start_time=i, duration=1) for i in range(25))
    editor = EditorController(ProjectState(notes=notes))
    assistant = _serialized_loop_assistant(tmp_path, editor, _SerializedLoopProvider([]))
    assistant._memory_accept("续写到25秒，不要循环", "EDIT_MUSIC")
    question = "刚才让你续写到25秒、不要重复开头，现在进度怎么样？请读取当前工程确认实际时长和音符，不要重新创作。"
    assert assistant._resolve_turn_intent(question) == "READ_MUSIC"
    context = assistant._create_execution_context("READ_MUSIC", question)
    assert context.authorized_bars == (BarRange(1, 13),)  # default120 BPM,2s per bar
    assert MusicPermission.WRITE_NOTES not in context.permissions
    assert not assistant._is_task_status_question("现在进度怎么样？继续写到30秒")
