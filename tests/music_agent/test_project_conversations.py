"""User regression: each project owns its conversation, including first save."""
from pathlib import Path

from PySide6.QtCore import QSettings

from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.editor.controller import EditorController
from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController, AssistantMessage, AssistantMessageKind
from ai_piano_studio.music_agent.conversation_history import ConversationHistory
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from ai_piano_studio.persistence.project_json import save_project
from ai_piano_studio.persistence.project_library import ProjectLibrary
from tests.music_agent.test_assistant_controller import _wait_until


def assistant_at(home, project=None):
    editor = EditorController(project_library=ProjectLibrary(home / "Projects"))
    if project:
        editor.loadProject(str(project))
    assistant = MusicAssistantController(editor, history_path=home / "assistant/conversation.sqlite3",
        provider_settings=ProviderSettingsService(settings=QSettings(str(home / "providers.ini"), QSettings.IniFormat),
            credentials=MemoryCredentialStore()))
    return assistant, editor


def say(assistant, text):
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, text))


def texts(assistant):
    return [m.text for m in assistant.messages.entries]


def project(home, name):
    path = home / (name + ".pianoai.json")
    save_project(path, ProjectState())
    return path


def test_switch_and_reopen_restore_only_the_selected_projects_chat(tmp_path, qapp):
    a, b = project(tmp_path, "A"), project(tmp_path, "B")
    assistant, editor = assistant_at(tmp_path, a)
    say(assistant, "A的旋律")
    editor.loadProject(str(b))
    assert texts(assistant) == []
    assert assistant._conversation_history() == ()
    say(assistant, "B的和弦")
    editor.loadProject(str(a))
    assert texts(assistant) == ["A的旋律"]
    assert [m["content"] for m in assistant._conversation_history()] == ["A的旋律"]
    reopened, _ = assistant_at(tmp_path, b)
    assert texts(reopened) == ["B的和弦"]


def test_new_project_has_no_chat_or_old_task_card(tmp_path, qapp):
    assistant, editor = assistant_at(tmp_path, project(tmp_path, "A"))
    say(assistant, "上一首的请求")
    assistant._task_progress.start(1, "上一首", "test", "test")
    assistant._task_progress.finish(1, "completed")
    editor.newProject()
    assert texts(assistant) == []
    assert assistant._conversation_history() == ()
    assert assistant.taskProgress == {}


def test_first_save_keeps_draft_chat_and_reopen_restores_it(tmp_path, qapp):
    assistant, editor = assistant_at(tmp_path)
    say(assistant, "草稿中的创作要求")
    assert editor.saveNamedProject("第一首")
    _wait_until(qapp, lambda: not editor.isSaving)
    path = editor.currentFilePath
    assert texts(assistant) == ["草稿中的创作要求"]
    editor.newProject()
    assert texts(assistant) == []
    editor.loadProject(path)
    assert texts(assistant) == ["草稿中的创作要求"]


def test_save_copy_starts_independent_chat_and_clear_does_not_erase_original(tmp_path, qapp):
    assistant, editor = assistant_at(tmp_path)
    say(assistant, "原工程的要求")
    assert editor.saveNamedProject("原工程")
    _wait_until(qapp, lambda: not editor.isSaving)
    original = editor.currentFilePath
    assert editor.saveCopy("新副本")
    _wait_until(qapp, lambda: not editor.isSaving)
    assert texts(assistant) == []
    say(assistant, "副本的要求")
    assistant.clearConversation()
    editor.loadProject(original)
    assert texts(assistant) == ["原工程的要求"]


def test_legacy_mixed_chat_is_retained_without_polluting_new_projects(tmp_path, qapp):
    old = ConversationHistory(tmp_path / "assistant/conversation.sqlite3")
    old.load()
    old.save(0, AssistantMessage(AssistantMessageKind.USER_MESSAGE, "旧的混合聊天").to_dict())
    assistant, editor = assistant_at(tmp_path)
    assert texts(assistant) == []
    say(assistant, "新草稿")
    assert [m["text"] for m in old.load()] == ["旧的混合聊天"]


def test_chat_view_keeps_conversation_and_errors_without_system_activity_rows(tmp_path, qapp):
    assistant, _ = assistant_at(tmp_path)
    for kind, text in [(AssistantMessageKind.USER_MESSAGE, "请修改"),
                       (AssistantMessageKind.SYSTEM_NOTICE, "已切换工程"),
                       (AssistantMessageKind.ACTIVITY, "已经读取120个音符"),
                       (AssistantMessageKind.ASSISTANT_MESSAGE, "旋律建议"),
                       (AssistantMessageKind.ERROR, "连接失败")]:
        assistant.messages.append(AssistantMessage(kind, text))
    view = getattr(assistant, "conversationMessages", assistant.messages)
    assert view.rowCount() == 3
    assert len(assistant.messages.entries) == 5, "technical records must be retained separately"
