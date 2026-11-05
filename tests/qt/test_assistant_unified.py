"""Actual QML regressions from the user's combined assistant report."""
import pytest
import json
from PySide6.QtCore import QObject, QPoint, QPointF, QCoreApplication, Qt
from PySide6.QtGui import QWheelEvent

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
from ai_piano_studio.music_agent.progress import ProviderProgressEvent
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, find_item, qml
from tools.studio_evidence import install_memory_output


@pytest.fixture(autouse=True)
def silent(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def workspace(session, qtbot):
    app, engine, root, editor, playback = session
    initialize_deferred_services(engine)
    root.resize(1440, 1000)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    click(root, 'studioAssistant')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    return root, engine.rootContext().contextProperty('assistant')


def test_ai_services_are_owned_by_assistant_not_general_settings(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    click(root, 'studioAssistantConnectionSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIServicePane') is not None)
    general = root.findChild(QObject, 'studioAppearanceDialog')
    assert general is None or not general.property('opened')
    service = root.findChild(QObject, 'assistantAIServiceDialog')
    assert service is not None and service.property('opened')
    qml(service, 'requestClose(); true')
    qtbot.waitUntil(lambda: not service.property('opened'))
    click(root, 'studioSettingsButton')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAppearanceDialog').property('opened'))
    assert find_item(root, 'studioSettingsCategory7') is None


def test_technical_activity_does_not_take_over_chat_and_finished_card_is_compact(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '修改前奏'))
    for i in range(25):
        assistant.messages.append(AssistantMessage(AssistantMessageKind.SYSTEM_NOTICE, f'处理步骤 {i}'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '旋律已准备，可以先试听。'))
    assistant._task_progress.start(1, '修改前奏', 'fixture', 'fixture')
    for stage in ('reading', 'generating', 'writing', 'verifying'):
        assistant._task_progress.record(1, ProviderProgressEvent(stage))
    assistant._task_progress.finish(1, 'completed')
    assistant.changed.emit()
    listing = find_item(root, 'assistantMessageList')
    qtbot.waitUntil(lambda: listing.property('count') == 2)
    card = find_item(root, 'assistantProgressCard')
    qtbot.wait(50)
    # Nov 4: the user requested removal of the duplicate fixed progress bar.
    # A legacy text-only response has no result card to occupy conversation space.
    assert card is None or not card.isVisible()
    assert listing.height() > 200
    assert len(assistant.messages.entries) == 27


def test_long_variable_height_chat_wheels_down_without_reversing_or_following_new_messages(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    for i in range(120):
        assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE,
            f'{i}: 正常对话\n' + '这是可阅读的旋律说明。\n' * (i % 13 + 1)))
    listing = find_item(root, 'assistantMessageList')
    qtbot.waitUntil(lambda: listing.property('count') == 120)
    qtbot.wait(80)
    pane = find_item(root, 'assistantWorkspace')
    pane.setProperty('followLatest', False)
    qml(listing, 'positionViewAtIndex(40, ListView.Center); true')
    qtbot.wait(80)
    def reading_position():
        return json.loads(qml(listing, 'JSON.stringify((function() { var y=contentY+height/2; var i=-1; for(var d=0; d<30 && i<0; d++) i=indexAt(10,y+d); var item=itemAtIndex(i); return [i, item ? y-item.y : -1]; })())'))
    def wheel(delta):
        point = listing.mapToScene(QPointF(listing.width() / 2, listing.height() / 2))
        event = QWheelEvent(point, point, QPoint(), QPoint(0, delta), Qt.NoButton,
            Qt.NoModifier, Qt.ScrollUpdate, False)
        QCoreApplication.sendEvent(root, event)
        qtbot.wait(35)
    for _ in range(5):
        before = reading_position()
        wheel(-120)
        after = reading_position()
        assert after > before, (before, after, listing.property('originY'))
    anchor = reading_position()
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '新的流式回复\n' * 30))
    qtbot.wait(80)
    assert reading_position() == pytest.approx(anchor, abs=1)
    assert not pane.property('followLatest')


def test_visible_audition_then_apply_controls_and_project_chat_drafts(studio_session, qtbot, tmp_path):
    root, assistant = workspace(studio_session, qtbot)
    from ai_piano_studio.music_agent.song_resolver import CATALOG_HOME, CatalogSourceProvider
    from ai_piano_studio.music_agent.providers import ProviderEvent
    from tests.qt.test_music_progress import _use_scripted_studio_provider
    from tests.music_agent.test_source_confirmation_workflow import call
    entry = CatalogSourceProvider(tmp_path).match('月光奏鸣曲')
    _use_scripted_studio_provider(assistant, tmp_path, [
        call('prepare_file_preview', dict(path=str(CATALOG_HOME / entry['file']))),
        (ProviderEvent(text_delta='月光奏鸣曲已准备好。', finish_reason='stop'),),
    ])
    editor = studio_session[3]
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '原工程中的对话'))
    input_box = find_item(root, 'assistantInput')
    input_box.setProperty('text', '原工程尚未发送的草稿')
    assert editor.saveNamedProject('原工程')
    qtbot.waitUntil(lambda: not editor.isSaving)
    old_path = editor.currentFilePath
    editor.newProject()
    assert input_box.property('text') == ''
    assert assistant.messages.entries == ()
    assert assistant.sendMessage('弹一首月光奏鸣曲')
    qtbot.waitUntil(lambda: assistant.taskPreviewAvailable, timeout=10000)
    qtbot.waitUntil(lambda: find_item(root, 'assistantTaskApply') is not None
                    and find_item(root, 'assistantTaskApply').isVisible())
    apply = find_item(root, 'assistantTaskApply')
    # User's November 4 creator contract removed compulsory audition. Keep the
    # real audition/adoption, score count, project isolation and draft checks.
    assert apply.isVisible() and apply.isEnabled()
    assert editor.currentFilePath == '' and not editor.state.playback_notes
    click(root, 'assistantTaskAudition')
    qtbot.waitUntil(lambda: assistant.taskPreviewPlayed, timeout=10000)
    assert apply.isEnabled() and not editor.state.playback_notes
    click(root, 'assistantTaskApply')
    qtbot.waitUntil(lambda: bool(editor.currentFilePath))
    assert len(editor.state.playback_notes) == 1142
    assert all('原工程中的对话' not in m.text for m in assistant.messages.entries)
    editor.loadProject(old_path)
    assert [m.text for m in assistant.messages.entries] == ['原工程中的对话']
    assert input_box.property('text') == '原工程尚未发送的草稿'


def test_connection_success_survives_visible_save_button(studio_session, qtbot, monkeypatch):
    from ai_piano_studio.music_agent.capabilities import ModelCapabilities
    root, assistant = workspace(studio_session, qtbot)
    class Provider:
        def test_connection(self):
            pass
        def verify_tool_capability(self):
            return ModelCapabilities(True, True, False, False, True)
    monkeypatch.setattr(assistant._provider_settings, '_provider_factory', lambda _id: Provider())
    assistant.selectProvider('ollama')
    assistant.saveProviderSettings('ollama', '', 'http://127.0.0.1:11434', '', 'llama3.2', False)
    click(root, 'studioAssistantConnectionSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIServicePane') is not None)
    qtbot.wait(80)
    test_button = find_item(root, 'studioAITestConnection')
    assert test_button.isEnabled()
    test_button.clicked.emit()
    qtbot.waitUntil(lambda: not assistant.connectionTesting and assistant.connectionResult['code'] == 'SUCCESS')
    label = find_item(root, 'studioAIConnectionResult')
    before = label.property('text')
    find_item(root, 'studioAISaveConnection').clicked.emit()
    assert assistant.connectionTested and assistant.connectionResult['code'] == 'SUCCESS'
    assert label.property('text') == before


def test_detached_audition_shows_notes_duration_and_following_cursor(studio_session, qtbot):
    from pathlib import Path
    from ai_piano_studio.domain.note import Note
    root, _ = workspace(studio_session, qtbot)
    _, _, _, editor, playback = studio_session
    before, depth = editor.state.content, editor.historyUndoDepth
    notes = tuple(Note.create(pitch=60 + i % 5, start_time=i, duration=.8) for i in range(20))
    playback.play_detached_notes(notes)
    qtbot.waitUntil(lambda: playback.playing)
    assert playback.totalSeconds == 19.8
    canvas = find_item(root, 'detachedPreviewCanvas')
    qtbot.waitUntil(lambda: canvas is not None and canvas.isVisible())
    assert len(qml(canvas, 'notes')) == 20
    # Drive the same position signal as PCM playback; follow must map it into
    # a real 19.8-second timeline rather than the empty project's zero range.
    playback.seek(12.0)
    timeline = find_item(root, 'timeline')
    if timeline is None:
        timeline = canvas.parentItem()
    qtbot.wait(100)
    playhead = find_item(root, 'playhead')
    assert playhead.isVisible() and playhead.x() > 72
    assert 12 <= playback.positionSeconds < 13
    assert canvas.isVisible() and playback.totalSeconds == 19.8
    assert editor.state.content == before and editor.historyUndoDepth == depth
    folder = Path('artifacts/audition-20261102'); folder.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(folder / 'preview.png'))
    playback.stop_detached_preview()
    assert not canvas.isVisible() and playback.totalSeconds == 0
    assert editor.state.content == before


def test_sending_from_old_history_reveals_newest_user_message(studio_session, qtbot, monkeypatch):
    root, assistant = workspace(studio_session, qtbot)
    for i in range(65):
        assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE,
            f"旧消息{i}：" + "这是较长的历史内容。" * (i % 9 + 1)))
    listing = find_item(root, "assistantMessageList")
    pane = find_item(root, "assistantWorkspace")
    qtbot.waitUntil(lambda: listing.property("count") == 65)
    qtbot.wait(100)
    pane.setProperty("followLatest", False)
    qml(listing, "positionViewAtIndex(10, ListView.Center); true")
    qtbot.wait(50)
    def local_send(text):
        assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, text))
        return True
    monkeypatch.setattr(assistant, "sendMessage", local_send)
    newest = "最新发言：保留周杰伦《青花瓷》的开头，只续写后面。" * 8
    find_item(root, "assistantInput").setProperty("text", newest)
    qml(pane, "sendAssistantMessage(); true")
    qtbot.waitUntil(lambda: listing.property("count") == 66)
    qtbot.wait(150)
    assert pane.property("followLatest")
    assert assistant.messages.entries[-1].text == newest
    assert qml(listing, "itemAtIndex(count-1) !== null && itemAtIndex(count-1).y + itemAtIndex(count-1).height <= contentY + height + 2")


def test_real_execution_receipt_is_visible_after_misleading_model_apply_advice(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, "已暂存，点击应用后生效。"))
    assistant._append_notice("执行回执：本轮修改已直接应用到工程，无需再次点击应用。")
    assistant._append_notice("技术处理步骤，不占聊天界面")
    listing = find_item(root, "assistantMessageList")
    qtbot.waitUntil(lambda: listing.property("count") == 2)
    qtbot.waitUntil(lambda: find_item(root, "assistantExecutionReceipt") is not None)
    receipt = find_item(root, "assistantExecutionReceipt")
    assert receipt is not None and receipt.isVisible()
    assert "已直接应用到工程" in receipt.property("text")


def test_creator_403_reply_keeps_visible_task_and_continue_control(studio_session, qtbot, tmp_path):
    from ai_piano_studio.music_agent.providers import ProviderEvent
    from tests.qt.test_music_progress import _use_scripted_studio_provider, visible_control
    root, assistant = workspace(studio_session, qtbot)
    answer = '下载站返回403，目前还没生成试听。'
    _use_scripted_studio_provider(assistant, tmp_path, [
        (ProviderEvent(text_delta=answer, finish_reason='stop'),),
        (ProviderEvent(text_delta='继续寻找可用谱源。', finish_reason='stop'),),
    ])
    assert assistant.sendMessage('烟花易冷30秒接晴天15秒，生成试听')
    qtbot.waitUntil(lambda: not assistant.isBusy, timeout=5000)
    assert assistant.taskProgress['status'] == 'responded'
    listing = find_item(root, 'assistantMessageList')
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskDetails') is not None)
    visible_control(listing, 'assistantTaskDetails').clicked.emit()
    qtbot.waitUntil(lambda: visible_control(listing, 'assistantTaskContinue') is not None)
    assert visible_control(listing, 'assistantTaskContinue').isEnabled()
    visible_control(listing, 'assistantTaskContinue').clicked.emit()
    qtbot.waitUntil(lambda: not assistant.isBusy, timeout=5000)
    assert any(m.text == '继续寻找可用谱源。' for m in assistant.messages.entries)

@pytest.mark.parametrize('width', [1000, 1440])
def test_composer_context_controls_and_icon_help(studio_session, qtbot, tmp_path, width):
    from PySide6.QtCore import QSettings
    root, assistant = workspace(studio_session, qtbot)
    assistant._window_settings = QSettings(str(tmp_path/'context.ini'), QSettings.IniFormat)
    assistant.changed.emit()
    root.resize(width, 900)
    qtbot.wait(100)
    button = find_item(root, 'assistantContextButton')
    assert button.isVisible()
    assert '全部' in button.property('text')
    click(root, 'assistantContextButton')
    popup = root.findChild(QObject, 'assistantContextPopup')
    qtbot.waitUntil(lambda: popup.property('opened'))
    field = find_item(root, 'assistantContextCharactersInput')
    qml(field, 'text = "12345"; editingFinished(); true')
    assert assistant.contextCharacters == 12345
    slider = find_item(root, 'assistantContextCharactersSlider')
    assert slider.property('value') == 12345
    qml(slider, 'value = 42000; moved(); true')
    assert assistant.contextCharacters == 42000
    assert field.property('text') == '42000'
    assert '42,000' in button.property('text') or '42000' in button.property('text')
    assert root.grabWindow().save(str(tmp_path/f'context-popup-{width}.png'))
    qml(popup, 'close(); true')
    from PySide6.QtTest import QTest
    attach = find_item(root, 'assistantAttachmentMenuButton')
    point = attach.mapToScene(QPointF(attach.width()/2, attach.height()/2)).toPoint()
    QTest.mouseMove(root, point)
    qtbot.waitUntil(lambda: qml(attach, 'ToolTip.toolTip.visible'), timeout=1500)
    assert qml(attach, 'ToolTip.toolTip.text') == '附件与导入'
    for name in ('assistantAttachmentMenuButton', 'studioAssistantQuickActions', 'assistantEditCreationScopeButton',
                 'assistantContextButton', 'assistantSendButton', 'studioAssistantConnectionSettings', 'studioAssistantClose'):
        item = find_item(root, name)
        assert qml(item, 'ToolTip.text.length > 0'), name
        if item.isVisible():
            position = item.mapToScene(QPointF())
            assert position.x() >= 0 and position.x() + item.width() <= root.width(), name


def test_only_inline_task_card_explains_continuing_and_waiting(studio_session, qtbot):
    root, assistant = workspace(studio_session, qtbot)
    assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, '夜的第七章20秒'))
    assistant.messages.append(AssistantMessage(AssistantMessageKind.ASSISTANT_MESSAGE, '暂时没找到，请上传文件。'))
    assistant._task_progress.start(91, '夜的第七章20秒', 'fixture', 'fixture')
    assistant._task_progress.set_model_driven(91)
    assistant._task_progress.report_model(91, dict(status='blocked', message='正在换来源'))
    assistant._assistant_status = 'thinking'
    assistant.changed.emit()
    qtbot.wait(100)
    cards = []
    def visit(item):
        if item.objectName() == 'assistantProgressCard' and item.isVisible():
            cards.append(item)
        for child in item.childItems():
            visit(child)
    visit(root.contentItem())
    assert len(cards) == 1
    notice = find_item(root, 'assistantContinuingNotice')
    assert notice.isVisible() and '仍在继续处理' in notice.property('text')
    assert '继续处理中' in cards[0].property('stateText')
    assistant._task_progress.set_waiting_for_user(91, True)
    qtbot.waitUntil(lambda: '等待你的回复' in notice.property('text'))
    assistant._assistant_status = 'idle'
    assistant._task_progress.finish(91, 'responded')
    assistant.changed.emit()
    qtbot.waitUntil(lambda: not notice.isVisible())
