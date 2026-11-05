"""Natural requests reach an isolated draft without a user-authored command form."""
import json

import pytest

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.providers import ProviderEvent, ProviderToolCall
from ai_piano_studio.music_agent.song_resolver import music_request
from tests.music_agent.test_creation_workflow import assistant_for, done, music_scripts


@pytest.mark.parametrize("instruction", [
    "来一首周杰伦的烟花易冷",
    "来一首林俊杰的江南",
    "给我来首陈奕迅的十年",
    "我想听孙燕姿的遇见",
])
def test_plain_song_request_routes_to_creation(instruction):
    assert MusicAssistantController._classify_intent(instruction) == "CREATE_MUSIC"


@pytest.mark.parametrize("instruction,artist,title", [
    ("来一首林俊杰的江南", "林俊杰", "江南"),
    ("给我来首陈奕迅的十年", "陈奕迅", "十年"),
    ("我想听孙燕姿的遇见", "孙燕姿", "遇见"),
    ("来一首五月天的温柔", "五月天", "温柔"),
    ("来点林俊杰的江南", "林俊杰", "江南"),
])
def test_song_identity_does_not_require_a_hardcoded_artist(instruction, artist, title):
    request = music_request(instruction)
    assert request.named and request.artist == artist and request.title == title


def test_one_plain_request_offers_real_isolated_notes_without_asking_for_parameters(tmp_path, qapp):
    editor = EditorController(ProjectState())
    assistant, playback, provider = assistant_for(tmp_path, editor, music_scripts())
    try:
        assert assistant.sendMessage("来一首周杰伦的烟花易冷")
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, assistant.taskProgress
        assert assistant.taskPreviewInfo["noteCount"] > 0
        assert not editor.state.active_notes and not playback.notes
        assert len([m for m in assistant.messages.entries if m.kind.value == "USER_MESSAGE"]) == 1
        # Controlled model notes verify delivery mechanics, not the requested song's identity.
        assert provider.requests
    finally:
        assistant.shutdown()


def test_missed_local_hint_cannot_report_song_success_with_no_draft(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent import resource_tools
    monkeypatch.setattr(resource_tools, "fetch_public_resource", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    scripts = [
        (ProviderEvent(tool_calls=(ProviderToolCall("source", "inspect_music_resource", json.dumps({
            "url": "https://scores.example.org/song", "title": "烟花易冷",
            "source_url": "https://scores.example.org/song",
        })),)),),
        (ProviderEvent(text_delta="暂时没有取得可用的曲谱。"),),
    ]
    assistant, _, _ = assistant_for(tmp_path, EditorController(ProjectState()), scripts)
    monkeypatch.setattr(assistant, "_resolve_turn_intent", lambda _: "CHAT")
    try:
        assert assistant.sendMessage("来一首周杰伦的烟花易冷")
        done(qapp, assistant)
        assert not assistant.taskPreviewAvailable
        assert assistant.taskProgress["status"] != "completed"
        assert assistant.lastTurnResult["status"] == "source_unavailable"
    finally:
        assistant.shutdown()


def test_provider_is_closed_when_a_music_turn_ends(tmp_path, qapp):
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), [
        (ProviderEvent(text_delta="你好。"),),
    ])
    closed = []
    provider.close = lambda: closed.append(True)
    try:
        assert assistant.sendMessage("你好")
        done(qapp, assistant)
        assert closed == [True]
    finally:
        assistant.shutdown()


def test_named_creation_can_deliver_a_resource_preview_in_one_user_send(tmp_path, qapp, monkeypatch):
    from ai_piano_studio.music_agent import resource_tools
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME
    monkeypatch.setattr(resource_tools, "fetch_public_resource", lambda url, **kw:
        ((CATALOG_HOME / "moonlight1.mid").read_bytes(), url, "audio/midi"))
    def call(name, args):
        return (ProviderEvent(tool_calls=(ProviderToolCall(name, name, json.dumps(args)),)),)
    assistant, _, _ = assistant_for(tmp_path, EditorController(ProjectState()), [
        call("inspect_music_resource", {"url": "https://scores.example.org/song.mid",
             "title": "烟花易冷", "source_url": "https://scores.example.org/edition"}),
        call("prepare_song_preview", {"resource_id": "resource-1", "start_seconds": 0, "duration_seconds": 30}),
    ])
    try:
        assert assistant.sendMessage("来一首周杰伦的烟花易冷")
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable, (assistant.taskProgress, assistant.lastTurnResult)
        assert assistant.taskPreviewInfo["durationSeconds"] == pytest.approx(30)
        assert not assistant._editor.state.active_notes
        assert len([m for m in assistant.messages.entries if m.kind.value == "USER_MESSAGE"]) == 1
        # Fixture music verifies the delivery path only; real song identity is tested separately.
    finally:
        assistant.shutdown()


def test_named_transcription_uses_careful_reasoning_without_changing_normal_chat(tmp_path, qapp):
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), music_scripts())
    seen = []
    provider.native_tools_enabled = True
    original = provider.stream_events
    def stream(messages, tools, reasoning, attachments):
        seen.append(reasoning)
        yield from original(messages, tools, reasoning, attachments)
    provider.stream_events = stream
    try:
        assert assistant.sendMessage('来一首周杰伦的烟花易冷')
        done(qapp, assistant)
        assert assistant.taskPreviewAvailable
        # Cross-song audits found clef/octave/rest errors at medium; named
        # reconstruction now prefers high unless the user chose an effort.
        assert seen and all(value == 'high' for value in seen)
    finally:
        assistant.shutdown()


@pytest.mark.parametrize('instruction', ['来一首周杰伦的烟花易冷', '来一首周杰伦的烟花易冷，30秒'])
def test_first_draft_context_preserves_duration_without_a_forced_short_excerpt(tmp_path, qapp, instruction):
    assistant, _, provider = assistant_for(tmp_path, EditorController(ProjectState()), music_scripts())
    try:
        assert assistant.sendMessage(instruction)
        done(qapp, assistant)
        prompt = json.dumps(provider.requests[0], ensure_ascii=False)
        # New capability instructions use source/user evidence, not a forced
        # 15-second script. Keep the actual request and latest-correction rule.
        assert '15-second' not in prompt
        assert 'section and duration using actual source evidence' in prompt
        assert 'latest supplements take precedence' in prompt
        assert 'prefer a 30-second' not in prompt
        assert instruction in prompt
    finally:
        assistant.shutdown()
