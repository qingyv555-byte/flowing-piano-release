from PySide6.QtCore import QObject
from threading import Event

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, find_item, qml
from tests.qt.test_nocturne import preserved_qsettings_state
from tools.studio_evidence import install_memory_output
import pytest


@pytest.fixture(autouse=True)
def silent_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def test_workspace_clear_requires_confirmation_and_cancel_preserves_history(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assert assistant._messages._history.path == tmp_path / 'assistant' / 'conversation.sqlite3'
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioAssistant')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    assistant._messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '保留这一条'))
    before = assistant._messages.entries
    # The compact workspace moved destructive actions into the More menu.
    click(root, 'assistantMoreButton')
    click(root, 'assistantClearConversationButton')
    dialog = root.findChild(QObject, 'assistantClearConversationDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    dialog.reject()
    assert assistant._messages.entries == before
    assert len(assistant._messages._history.load()) == len(before)
    click(root, 'assistantMoreButton')
    click(root, 'assistantClearConversationButton')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    dialog.accept()
    assert assistant._messages.entries == ()
    assert assistant._messages._history.load() == []


def test_execution_receipt_and_separate_connection_capability_status(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioAssistant')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    assistant._last_turn_result = {
        'intent': 'READ_MUSIC', 'toolCallCount': 2, 'readCount': 2, 'writeCount': 0,
        'noteReads': [{'startBar': 1, 'endBar': 4, 'count': 9}], 'projectNoteCount': 17,
    }
    # Receipts now belong to task details, rather than the project-info panel.
    # A local running task exposes the real card without submitting a model call.
    assistant._generation = assistant._active_generation = 43
    assistant._active_cancel = Event()
    assistant._assistant_status = 'thinking'
    assistant._task_progress.start(43, '读取音乐', 'fixture', 'fixture')
    assistant._task_progress.set_model_driven(43)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '读取音乐'))
    assistant.changed.emit()
    qtbot.waitUntil(lambda: find_item(root, 'assistantTaskDetails') is not None
                    and find_item(root, 'assistantTaskDetails').isVisible())
    toggle = find_item(root, 'assistantTaskDetails')
    card = toggle.parentItem().parentItem().parentItem()
    details = card.findChild(QObject, 'assistantTaskExpandedDetails')
    receipt = next(item for item in details.findChildren(QObject)
                   if item.property('text') == assistant.executionSummary)
    assert not receipt.isVisible()
    click(root, 'assistantTaskDetails')
    qtbot.waitUntil(receipt.isVisible)
    assert '第 1–4 小节' in receipt.property('text')
    assert '9 个' in receipt.property('text') and '17 个' in receipt.property('text')
    connection = root.findChild(QObject, 'studioAssistantConnectionStatus')
    workspace = root.findChild(QObject, 'assistantWorkspace')
    assert connection.property('text') == '● ' + workspace.property('compactConnectionLabel')
    assert assistant.currentModelStatus['detail'] in qml(connection, 'Accessible.description')
    assert assistant.connectionResult['message'] in qml(connection, 'Accessible.description')
    assistant.cancelGeneration()
