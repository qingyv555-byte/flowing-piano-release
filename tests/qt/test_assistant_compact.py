"""Compact assistant interactions with real QML and isolated, silent projects."""
import pytest
from pathlib import Path
from threading import Event
from PySide6.QtCore import QObject, QCoreApplication, QPoint, Qt
from PySide6.QtGui import QInputMethodEvent, QWindow
from PySide6.QtTest import QTest

from ai_piano_studio.domain.note import Note
from ai_piano_studio.domain.project import ProjectState
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from ai_piano_studio.music_agent.song_workflow import prepare_project
from tests.qt.test_assistant_unified import silent, workspace
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, qml


def offer_song(assistant, editor, tmp_path, title='月光练习'):
    assistant._generation = 1
    assistant._task_message_start_row = len(assistant.messages.entries)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '先写一段原创，再试听'))
    workflow = assistant._creation_workflow
    workflow.generation = 1
    workflow.message_start_row = assistant._task_message_start_row
    workflow.baseline = (editor.projectSessionId, editor.projectRevision)
    assistant._task_progress.start(1, '月光练习', 'fixture', 'fixture')
    state = ProjectState(tuple(Note.create(pitch=60 + i % 6, start_time=i, duration=.5) for i in range(20)))
    workflow._offer_review(prepare_project(state, title, {'kind': 'ai_original'}, tmp_path))
    return workflow


@pytest.mark.parametrize('width', [380, 440, 520])
@pytest.mark.parametrize('size', [(1366, 768), (1920, 1080)])
def test_compact_card_and_composer_leave_room_for_chat(studio_session, qtbot, tmp_path, width, size):
    root, assistant = workspace(studio_session, qtbot)
    root.resize(*size)
    root.setProperty('studioAssistantWidth', width)
    editor = studio_session[3]
    before = editor.state.content
    offer_song(assistant, editor, tmp_path)
    qtbot.waitUntil(lambda: find_item(root, 'assistantTaskApply') is not None
                    and find_item(root, 'assistantTaskApply').isVisible())
    listing = find_item(root, 'assistantMessageList')
    composer = find_item(root, 'assistantComposerPanel')
    assert listing.height() >= 350
    assert 86 <= composer.height() <= 100
    assert composer.width() <= 410
    card = find_item(root, 'assistantTaskApply').parentItem().parentItem().parentItem()
    assert 72 <= card.height() <= 96
    for name in ('assistantInput', 'assistantSendButton', 'assistantTaskAudition', 'assistantTaskApply'):
        item = find_item(root, name)
        point = item.mapToScene(item.boundingRect().topLeft())
        assert point.x() >= root.width() - width
        assert point.x() + item.width() <= root.width()
        assert 0 <= point.y() <= root.height() - item.height()
    assert editor.state.content == before


def test_composer_multiline_ime_enter_and_stop(studio_session, qtbot, monkeypatch):
    root, assistant = workspace(studio_session, qtbot)
    field = find_item(root, 'assistantInput')
    composer = find_item(root, 'assistantComposerPanel')
    sent = []
    monkeypatch.setattr(assistant, '_send_message', lambda text, **kwargs: sent.append(text) or False)
    field.setProperty('text', '讨论旋律')
    field.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return, Qt.ShiftModifier)
    assert '\n' in field.property('text') and not sent
    QTest.keyClick(root, Qt.Key_Return)
    assert len(sent) == 1
    field.setProperty('text', '多行输入\n' * 30)
    qtbot.wait(30)
    assert 150 <= composer.height() <= 164
    QCoreApplication.sendEvent(field, QInputMethodEvent('输入中的文字', []))
    assert field.property('inputMethodComposing')
    QTest.keyClick(root, Qt.Key_Return)
    assert len(sent) == 1
    QCoreApplication.sendEvent(field, QInputMethodEvent())
    assistant._assistant_status = 'thinking'
    assistant.changed.emit()
    assert field.isEnabled()
    click(root, 'assistantStopButton')
    assert not assistant.isBusy


@pytest.mark.parametrize('width', [380, 440, 520])
def test_busy_composer_sends_supplements_and_centers_the_separate_stop_icon(studio_session, qtbot, width):
    root, assistant = workspace(studio_session, qtbot)
    root.setProperty('studioAssistantWidth', width)
    assistant._generation = assistant._active_generation = 42
    assistant._active_cancel = Event()
    assistant._assistant_status = 'thinking'
    assistant._retry_instruction = '来一段10s的青花瓷'
    assistant._task_progress.start(42, '来一段10s的青花瓷', 'fixture', 'fixture')
    assistant.changed.emit()
    field = find_item(root, 'assistantInput')
    qtbot.waitUntil(lambda: field.isEnabled())
    field.setProperty('text', '改成15秒')
    click(root, 'assistantSendButton')
    assert assistant.isBusy and assistant.pendingMessageCount == 1 and field.property('text') == ''
    field.setProperty('text', '副歌部分，轻柔一点')
    field.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Return)
    assert assistant.pendingMessageCount == 2 and field.property('text') == ''
    stop = find_item(root, 'assistantStopButton')
    icon = find_item(root, 'assistantStopIcon')
    send = find_item(root, 'assistantSendButton')
    assert stop.isVisible() and icon.isVisible()
    icon_center = icon.mapToScene(icon.boundingRect().center())
    stop_center = stop.mapToScene(stop.boundingRect().center())
    assert icon_center.x() == pytest.approx(stop_center.x(), abs=.1)
    assert icon_center.y() == pytest.approx(stop_center.y(), abs=.1)
    assert send.mapToScene(send.boundingRect().topRight()).x() < stop.mapToScene(stop.boundingRect().topLeft()).x()
    field.setProperty('text', '尚未发送的草稿')
    if width == 440:
        qtbot.wait(40)
        evidence = Path('artifacts/assistant-supplements')
        evidence.mkdir(parents=True, exist_ok=True)
        suffix = '' if root.devicePixelRatio() == 1 else f'-dpr{root.devicePixelRatio():g}'
        assert root.grabWindow().save(str(evidence / f'busy-with-supplements{suffix}.png'))
    click(root, 'assistantStopButton')
    assert not assistant.isBusy and assistant.pendingMessageCount == 0
    assert field.property('text') == '尚未发送的草稿'


def test_progress_arrow_directly_expands_and_collapses_task_details(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    assistant._generation = assistant._active_generation = 43
    assistant._active_cancel = Event()
    assistant._assistant_status = 'thinking'
    assistant._task_progress.start(43, '找谱中', 'fixture', 'fixture')
    assistant._task_progress.set_model_driven(43)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '找谱中'))
    assistant.changed.emit()
    # Task cards now belong to a transcript row, not a global floating panel.
    qtbot.waitUntil(lambda: find_item(root, 'assistantTaskDetails') is not None
                    and find_item(root, 'assistantTaskDetails').isVisible())
    toggle = find_item(root, 'assistantTaskDetails')
    card = toggle.parentItem().parentItem().parentItem()
    details = card.findChild(QObject, 'assistantTaskExpandedDetails')
    height = card.height()
    assert not card.property('expanded') and not details.isVisible()
    assert '···' not in toggle.property('text')
    click_item(root, toggle)
    qtbot.waitUntil(lambda: card.property('expanded') and details.isVisible() and card.height() > height)
    evidence = Path('artifacts/assistant-supplements')
    evidence.mkdir(parents=True, exist_ok=True)
    suffix = '' if root.devicePixelRatio() == 1 else f'-dpr{root.devicePixelRatio():g}'
    assert root.grabWindow().save(str(evidence / f'progress-expanded{suffix}.png'))
    click_item(root, toggle)
    qtbot.waitUntil(lambda: not card.property('expanded') and not details.isVisible()
                    and abs(card.height() - height) <= 1)
    assert card.height() == pytest.approx(height, abs=1)
    assistant.cancelGeneration()


def test_scope_attachment_and_clear_are_reachable_from_menus(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    click(root, 'assistantEditCreationScopeButton')
    click(root, 'assistantScopeManual')
    scope = root.findChild(QObject, 'assistantCreationScopeDialog')
    qtbot.waitUntil(lambda: scope.property('opened'))
    find_item(root, 'assistantCreationScopeStartBar').setProperty('text', '5')
    find_item(root, 'assistantCreationScopeEndBar').setProperty('text', '8')
    click(root, 'assistantCreationScopeConfirmButton')
    qtbot.waitUntil(lambda: not scope.property('opened'))
    assert assistant.creationScope['startBar'] == 5
    assert assistant.creationScope['endBar'] == 8
    assert '5–8' in find_item(root, 'assistantEditCreationScopeButton').property('text')
    imports = []
    assistant.audioImportRequested.connect(lambda: imports.append(True))
    click(root, 'assistantAttachmentMenuButton')
    click(root, 'assistantAttachButton')
    assert imports == [True]
    # Close the application's real import dialog before opening another modal.
    audio = root.findChild(QObject, 'audioImportFileDialog')
    assert audio is not None
    qml(audio, 'close(); true')
    qtbot.waitUntil(lambda: not audio.property('visible'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '保留到明确清空'))
    click(root, 'assistantMoreButton')
    click(root, 'assistantClearConversationButton')
    dialog = root.findChild(QObject, 'assistantClearConversationDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert assistant.messages.entries
    qml(dialog, 'reject(); true')
    assert assistant.messages.entries
    qml(dialog, 'open(); true')
    qml(dialog, 'accept(); true')
    assert not assistant.messages.entries and not assistant.taskDisplayReceipts


def test_result_details_and_draft_survive_detach_and_apply(studio_session, qtbot, tmp_path):
    root, assistant = workspace(studio_session, qtbot)
    workflow = offer_song(assistant, studio_session[3], tmp_path)
    qtbot.waitUntil(lambda: bool(assistant.taskDisplayReceipts))
    find_item(root, 'assistantInput').setProperty('text', '继续这段旋律')
    click(root, 'assistantMoreButton')
    click(root, 'assistantDetachButton')
    qtbot.waitUntil(lambda: root.property('studioAssistantDetached'))
    detached = root.findChild(QWindow, 'assistantWindow')
    qtbot.waitUntil(lambda: detached is not None and detached.isVisible())
    qtbot.waitUntil(lambda: find_item(detached, 'assistantTaskApply') is not None
                    and find_item(detached, 'assistantTaskApply').isVisible())
    assert find_item(detached, 'assistantInput').property('text') == '继续这段旋律'
    # Creator mode permits direct adoption; listening is optional.
    assert assistant.creatorEnabled
    assert find_item(detached, 'assistantTaskApply').isEnabled()
    detached.close()
    qtbot.waitUntil(lambda: not root.property('studioAssistantDetached'))
    assert find_item(root, 'assistantInput').property('text') == '继续这段旋律'
    click(root, 'assistantMoreButton')
    click(root, 'assistantDetachButton')
    qtbot.waitUntil(lambda: root.property('studioAssistantDetached'))
    detached = root.findChild(QWindow, 'assistantWindow')
    qtbot.waitUntil(lambda: detached is not None and detached.isVisible())
    qtbot.waitUntil(lambda: find_item(detached, 'assistantTaskApply') is not None
                    and find_item(detached, 'assistantTaskApply').isVisible())
    workflow.previewed = True
    assistant.changed.emit()
    click(detached, 'assistantTaskApply')
    qtbot.waitUntil(lambda: bool(studio_session[3].currentFilePath))
    qtbot.waitUntil(lambda: any(r['previewInfo'].get('title') == '月光练习' and r['saved']
                              for r in assistant.taskDisplayReceipts.values()))
    detached.close()
    qtbot.waitUntil(lambda: not root.property('studioAssistantDetached'))
    assert find_item(root, 'assistantInput').property('text') == ''  # New song has its own draft.
    assert len(studio_session[3].state.playback_notes) == 20


def test_unsaved_connection_guard_preserves_docked_close(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    click(root, 'studioAssistantConnectionSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIServicePane') is not None)
    pane = root.findChild(QObject, 'studioAIServicePane')
    qml(pane, 'newConnection(); true')
    assert pane.property('dirty')
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    click(root, 'assistantAIServiceDismiss')
    guard = root.findChild(QObject, 'studioAIUnsavedDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIKeepEditing')
    assert dialog.property('opened') and pane.property('dirty')
    # Closing the owning workspace also uses the same save/discard guard.
    qml(root.findChild(QObject, 'assistantWorkspace'), 'requestWindowClose(function() { closeRequested() }); true')
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIDiscardChanges')
    qtbot.waitUntil(lambda: not root.property('studioAssistantOpen'))


def test_resize_handle_and_feedback_leave_composer_accessible(studio_session, qtbot):
    root, _ = workspace(studio_session, qtbot)
    editor = studio_session[3]
    editor.createNoteAt(0, 0)
    before = editor.state.content
    root.resize(1366, 768)
    qtbot.wait(30)
    handle = find_item(root, 'assistantSidebarResizeHandle')
    handle.forceActiveFocus(Qt.TabFocusReason)
    qtbot.waitUntil(lambda: handle.hasActiveFocus())
    QTest.keyClick(root, Qt.Key_Left)
    assert root.property('studioAssistantWidth') == 460
    QTest.keyClick(root, Qt.Key_Right)
    assert root.property('studioAssistantWidth') == 440
    point = handle.mapToScene(handle.boundingRect().center()).toPoint()
    QTest.mousePress(root, Qt.LeftButton, pos=point)
    QTest.mouseMove(root, point - QPoint(40, 0), delay=20)
    QTest.mouseRelease(root, Qt.LeftButton, pos=point - QPoint(40, 0))
    assert root.property('studioAssistantWidth') == pytest.approx(480, abs=1)
    assert editor.state.content == before
    host = find_item(root, 'studioAssistantHost')
    qml(root, 'showFeedback("success", "已采用作品"); true')
    toast = find_item(root, 'feedbackSurface')
    qtbot.waitUntil(lambda: toast.isVisible())
    point = toast.mapToScene(toast.boundingRect().topLeft())
    assert point.x() + toast.width() <= root.width() - host.width()


def test_long_result_title_wraps_and_details_remain_reachable(studio_session, qtbot, tmp_path):
    root, assistant = workspace(studio_session, qtbot)
    root.resize(1366, 768)
    root.setProperty('studioAssistantWidth', 380)
    title = '月光下的钢琴练习与温柔的分解和弦伴奏，给准备入睡的人' * 2
    offer_song(assistant, studio_session[3], tmp_path, title)
    qtbot.waitUntil(lambda: find_item(root, 'assistantTaskApply') is not None
                    and find_item(root, 'assistantTaskApply').isVisible())
    apply = find_item(root, 'assistantTaskApply')
    card = apply.parentItem().parentItem().parentItem()
    label = card.findChild(QObject, 'assistantProgressStatus')
    assert title in label.property('text')
    assert label.height() > 32
    assert label.mapToScene(label.boundingRect().bottomLeft()).y() <= apply.mapToScene(apply.boundingRect().topLeft()).y()
    click_item(root, card.findChild(QObject, 'assistantTaskDetails'))
    assert card.property('expanded')
    assert card.height() > label.height()
