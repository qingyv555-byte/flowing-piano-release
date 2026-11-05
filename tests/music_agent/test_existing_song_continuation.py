"""Continuation is an edit of the adopted project, never a fresh reconstruction."""
import json

import pytest

from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from tests.music_agent.test_imported_song_extension import adopted_assistant
from tests.music_agent.test_creation_workflow import done


@pytest.mark.parametrize("text", [
    "按原曲往下接，写到25秒",
    "按原曲往下接，写到25秒。。midi文件你自己找",
    "按原曲往下接到25秒，直接修改进去",
    "接下去到25秒，保留开头",
    "往后接到25秒，先试听",
])
def test_natural_continuation_duration_is_an_edit(text):
    assert MusicAssistantController._duration_extension_seconds(text) == 25
    assert MusicAssistantController._classify_intent(text) == "EDIT_MUSIC"


@pytest.mark.parametrize("direct", [False, True])
def test_adopted_song_continuation_never_starts_new_song(tmp_path, qapp, direct):
    editor, assistant, provider, source = adopted_assistant(tmp_path, qapp)
    baseline = editor.state.active_notes
    original_path = editor.currentFilePath
    def forbidden_new_song(*args, **kwargs):
        raise AssertionError("Existing song continuation must not resolve a new isolated song")
    assistant._creation_workflow.resolver.resolve = forbidden_new_song
    notes = [dict(pitch=64+i%4, start_time=i, duration=1, velocity=70,
                  layer_id=editor.state.layers[0].id) for i in range(10, 25)]
    provider._scripts.append((ProviderEvent(tool_calls=(ProviderToolCall("continue", "add_notes",
        json.dumps({"notes": notes})),)),))
    text = "按原曲往下接，写到25秒" + ("，直接修改进去" if direct else "")
    assert assistant.sendMessage(text)
    done(qapp, assistant)
    assert not assistant.taskPreviewAvailable
    assert editor.currentFilePath == original_path
    if not direct:
        assert editor.state.playback_duration == 10
        assert assistant.proposal["status"] == "pending"
        assert assistant.applyProposal()["success"]
    editor.synchronizeNow()
    assert editor.state.playback_duration == 25
    by_id = {n.id: n for n in editor.state.active_notes}
    for old in baseline:
        actual = by_id[old.id]
        assert (actual.pitch, actual.start_time, actual.duration, actual.velocity, actual.layer_id) == (
            old.pitch, old.start_time, old.duration, old.velocity, old.layer_id)
    assert assistant._task_memory.state["constraints"]["targetSeconds"] == 25


def test_status_negation_and_new_composition_stay_distinct():
    assert MusicAssistantController._duration_extension_seconds("不要往下接到25秒，先分析") is None
    assert MusicAssistantController._classify_intent("另写一首25秒原创钢琴曲") == "CREATE_MUSIC"
    assert MusicAssistantController._is_task_status_question("刚才让你按原曲往下接到25秒，怎么没动？")


def test_task_reply_after_a_supplement_is_appended_in_chat_order(tmp_path, qapp):
    from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
    _, assistant, _, _ = adopted_assistant(tmp_path, qapp)
    assistant._creation_reply("先前的试听结果")
    row = assistant._creation_workflow.response_row
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, "你把开头改了，保留原曲"))
    assistant._creation_reply("收到，保留现有开头")
    assert assistant.messages.entries[row].text == "先前的试听结果"
    assert [m.text for m in assistant.messages.entries[-2:]] == ["你把开头改了，保留原曲", "收到，保留现有开头"]


def test_continuation_without_duration_still_protects_existing_music(tmp_path, qapp):
    _, assistant, _, _ = adopted_assistant(tmp_path, qapp)
    assert assistant._create_execution_context("EDIT_MUSIC", "保留开头，接着往下续写").append_after_seconds == 10
    assert assistant._create_execution_context("EDIT_MUSIC", "不要续写，把开头力度改低").append_after_seconds is None


def test_original_song_continuation_prompt_does_not_allow_guessing(tmp_path, qapp):
    from ai_piano_studio.music_agent.context_builder import MusicAgentContextBuilder
    _, assistant, _, _ = adopted_assistant(tmp_path, qapp)
    context = assistant._create_execution_context("EDIT_MUSIC", "按原曲往下接，写到25秒")
    prompt = MusicAgentContextBuilder(assistant._gateway).build(
        "周杰伦《青花瓷》，按原曲往下接，写到25秒", context, intent="EDIT_MUSIC")[0]["content"]
    assert "Do not guess" in prompt
    assert "best-effort reconstruction" not in prompt


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("action", ["rewrite", "prepend"])
def test_continuation_rejects_changes_to_the_existing_song(tmp_path, qapp, monkeypatch, direct, action):
    editor, assistant, provider, _ = adopted_assistant(tmp_path, qapp)
    before = editor.state.content
    results = []
    execute = assistant._gateway.execute
    def recorded(*args, **kwargs):
        result = execute(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(assistant._gateway, "execute", recorded)
    bad = (ProviderToolCall("bad", "set_note_velocity", json.dumps({
        "note_ids": [editor.state.active_notes[0].id], "velocity": 30})) if action == "rewrite"
        else ProviderToolCall("bad", "add_notes", json.dumps({"notes": [
            {"pitch": 72, "start_time": 0, "duration": 1, "velocity": 70}]})))
    provider._scripts.append((ProviderEvent(tool_calls=(bad,)),))
    assert assistant.sendMessage("按原曲往下接，写到25秒" + ("，直接修改进去" if direct else ""))
    done(qapp, assistant)
    assert editor.state.content == before
    assert not assistant.proposal["available"]
    assert any(not r.success and r.error_code == "validation_error"
               and "continuation" in r.summary for r in results)
