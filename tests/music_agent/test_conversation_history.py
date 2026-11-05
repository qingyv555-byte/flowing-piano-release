from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import (
    AssistantMessage, AssistantMessageKind, MusicAssistantController,
)
from ai_piano_studio.music_agent.tool_contracts import BarRange, MusicPermission
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.music_agent.agent_loop import MusicAgentLoopOutcome
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.domain.project import ProjectState


def controller(path: Path):
    # Restarting the same saved project retains its chat. A new unsaved
    # project now intentionally starts a different conversation.
    project = path.parent / 'conversation-fixture.pianoai.json'
    project.parent.mkdir(parents=True, exist_ok=True)
    if not project.exists():
        save_project(project, ProjectState())
    editor = EditorController()
    editor.loadProject(str(project))
    return MusicAssistantController(
        editor, history_path=path,
        provider_settings=ProviderSettingsService(
            settings=QSettings(str(path.parent / 'providers.ini'), QSettings.IniFormat),
            credentials=MemoryCredentialStore(),
        ),
    )


def test_history_survives_restart_replaces_stream_and_requires_explicit_clear(tmp_path, qapp):
    path = tmp_path / 'workspace' / 'conversation.sqlite3'
    first = controller(path)
    first._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '创作一段旋律'))
    first._messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '正在'))
    first._messages.replace(1, AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '已完成'))
    first.clear_temporary_conversation()
    assert first._conversation_history() == ()
    first.shutdown()
    second = controller(path)
    assert [m.text for m in second._messages.entries] == ['创作一段旋律', '已完成']
    assert controller(tmp_path / 'other.sqlite3')._messages.entries == ()
    second.clearConversation()
    assert controller(path)._messages.entries == ()


def test_restored_proposal_is_read_only_and_does_not_restore_permission(tmp_path, qapp):
    path = tmp_path / 'history.sqlite3'
    first = controller(path)
    first._messages.append(AssistantMessage(AssistantMessageKind.PROPOSAL, '新增17个音符', '建议', '第1–9小节', 'old-id'))
    restored = controller(path)
    entry, = restored._messages.entries
    assert entry.kind == AssistantMessageKind.SYSTEM_NOTICE
    assert '新增17个音符' in entry.text and '第1–9小节' in entry.text
    assert not entry.proposal_id
    assert restored._last_music_intent == ''


def test_unreadable_history_is_preserved_and_problem_is_visible(tmp_path, qapp):
    path = tmp_path / 'broken.sqlite3'
    original = b'not a database'
    path.write_bytes(original)
    assistant = controller(path)
    assert assistant.conversationError
    assistant._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '新的消息'))
    assert path.read_bytes() == original
    assert assistant._messages.entries[0].text == '新的消息'


def test_large_history_is_not_pruned_for_model_context(tmp_path, qapp):
    path = tmp_path / 'history.sqlite3'
    assistant = controller(path)
    for i in range(180):
        assistant._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, str(i)))
    assert len(controller(path)._messages.entries) == 180


@pytest.mark.parametrize('text', ['我要青花瓷原曲', '我要原曲，不要原创', '换成原版旋律'])
def test_original_song_correction_keeps_previous_music_task(tmp_path, qapp, text):
    assistant = controller(tmp_path / 'history.sqlite3')
    assert assistant._resolve_turn_intent(text) in {'CREATE_MUSIC', 'EDIT_MUSIC'}
    assistant._last_music_intent = 'CREATE_MUSIC'
    assert assistant._resolve_turn_intent(text) in {'CREATE_MUSIC', 'EDIT_MUSIC'}


@pytest.mark.parametrize('text', ['我要原版手机', '原曲是什么意思', '你喜欢青花瓷原曲吗', '我要青花瓷原曲的介绍'])
def test_original_song_chatter_does_not_gain_write_access(tmp_path, qapp, text):
    assistant = controller(tmp_path / 'history.sqlite3')
    assistant._last_music_intent = 'CREATE_MUSIC'
    assert assistant._resolve_turn_intent(text) not in {'CREATE_MUSIC', 'EDIT_MUSIC'}


def test_original_song_correction_reuses_previous_authorization(tmp_path, qapp):
    assistant = controller(tmp_path / 'history.sqlite3')
    original = assistant._create_execution_context('CREATE_MUSIC', '在第20–24小节创作旋律')
    assistant._remember_music_context(original, 'CREATE_MUSIC')
    correction = assistant._create_execution_context(assistant._resolve_turn_intent('我要青花瓷原曲'), '我要青花瓷原曲')
    assert correction.authorized_bars == (BarRange(20, 24),)
    assert correction.permissions == original.permissions
    assert MusicPermission.WHOLE_PROJECT_WRITE not in correction.permissions


def test_next_tool_round_replaces_interim_text_in_one_bubble(tmp_path, qapp):
    assistant = controller(tmp_path / 'history.sqlite3')
    assistant._active_generation = 1
    assistant._active_intent = 'READ_MUSIC'
    assistant._on_generation_text_delta(1, '我先检查项目。')
    assistant._on_generation_response_started(1)
    assistant._on_generation_text_delta(1, '已读到')
    assistant._on_generation_text_delta(1, '9个音符。')
    assert [entry.text for entry in assistant._messages.entries] == ['已读到9个音符。']
    assert controller(tmp_path / 'history.sqlite3')._messages.entries[0].text == '已读到9个音符。'


def test_request_for_score_returns_to_idle_without_claiming_an_edit(tmp_path, qapp):
    assistant = controller(tmp_path / 'history.sqlite3')
    context = assistant._create_execution_context('CREATE_MUSIC', '我要青花瓷原曲')
    before = assistant._editor.state
    assistant._active_generation = 7
    assistant._active_intent = 'CREATE_MUSIC'
    assistant._active_context = context
    assistant._on_generation_finished(7, MusicAgentLoopOutcome(
        status='needs_input', text='请提供对应的简谱或五线谱。', context=context), None)
    assert assistant.assistantStatus == 'idle'
    assert assistant._messages.entries[-1].text == '请提供对应的简谱或五线谱。'
    assert assistant._editor.state == before
    assert not any(entry.kind == AssistantMessageKind.ERROR for entry in assistant._messages.entries)
