"""Production workflow review, offscreen and with in-memory credentials only."""
import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, qml
from tools.studio_evidence import install_memory_output


@pytest.fixture(autouse=True)
def memory_audio(monkeypatch):
    blocked = install_memory_output(monkeypatch.setattr)
    yield
    assert not blocked


def reveal(root, name):
    item = find_item(root, name)
    assert item is not None
    scroll = root.findChild(QObject, 'assistantAIServiceScroll')
    flick = qml(scroll, 'contentItem')
    y = item.mapToItem(flick, QPointF()).y()
    if y < 0 or y + item.height() > flick.height():
        maximum = max(0, flick.property('contentHeight') - flick.height())
        flick.setProperty('contentY', min(maximum, max(0, flick.property('contentY') + y - 30)))
    return item


def open_ai_settings(root, engine, qtbot):
    initialize_deferred_services(engine)
    qml(root, 'openAIServiceSettings(); true')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIServicePane') is not None)
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert dialog.property('opened')
    return root.findChild(QObject, 'studioAIServicePane')


def test_history_context_setting_reaches_next_model_request(studio_session, qtbot, tmp_path):
    from ai_piano_studio.music_agent.assistant_controller import AssistantMessage, AssistantMessageKind
    from ai_piano_studio.music_agent.providers import ProviderEvent
    from tests.qt.test_music_progress import _use_scripted_studio_provider

    _, engine, root, _, _ = studio_session
    open_ai_settings(root, engine, qtbot)
    assistant = engine.rootContext().contextProperty('assistant')
    settings_path = str(tmp_path / 'assistant-window.ini')
    assistant._window_settings = QSettings(settings_path, QSettings.IniFormat)
    assistant.changed.emit()
    spin = reveal(root, 'studioAIHistoryContextKB')
    assert spin.property('value') == 48
    spin.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_Up)
    qtbot.waitUntil(lambda: assistant.historyContextKB == 52)
    assistant._window_settings.sync()
    assert QSettings(settings_path, QSettings.IniFormat).value('assistant/historyContextKB', type=int) == 52

    provider = _use_scripted_studio_provider(assistant, tmp_path, [
        (ProviderEvent(text_delta='收到。', finish_reason='stop'),),
    ])
    prior_messages = [f'旧要求 {index}：' + '谱' * 499 for index in range(40)]
    for message in prior_messages:
        assistant.messages.append(AssistantMessage(AssistantMessageKind.USER_MESSAGE, message))
    assert assistant.sendMessage('聊聊音乐吧')
    qtbot.waitUntil(lambda: not assistant.isBusy)
    sent = [message['content'] for message in provider.requests[0]]
    assert prior_messages[7] in sent  # Outside the default history, inside the expanded one.
    assert sum(len(message.encode()) for message in sent if message.startswith('旧要求')) > 48_000


@pytest.mark.parametrize('width', [1000, 1280, 1440])
def test_docked_assistant_keeps_draft_and_music_across_settings(studio_session, qtbot, tmp_path, width):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    root.resize(width, 800)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    editor.createNoteAt(0, 0)
    editor.synchronizeNow()
    original, depth = editor.state.content, editor.historyUndoDepth
    click(root, 'studioAssistant')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    workspace = root.findChild(QObject, 'assistantWorkspace')
    host = root.findChild(QObject, 'studioAssistantHost')
    assert workspace.parentItem() == host
    assert not root.findChildren(QWindow, 'assistantWindow')
    assert 400 <= host.width() <= 480
    for name in ('studioAssistantModelSelector', 'assistantInput', 'assistantSendButton',
                 'assistantAttachmentMenuButton', 'studioAssistantModeButton', 'studioAssistantQuickActions'):
        item = find_item(root, name)
        assert item.isVisible(), name
        p = item.mapToScene(QPointF())
        assert host.mapToScene(QPointF()).x() <= p.x(), name
        assert p.x() + item.width() <= root.width(), name
        assert p.y() + item.height() < root.height() - 50, name
    # Import is a menu action in the compact composer; verify actual reachability
    # and viewport bounds at every supported width instead of requiring it inline.
    click(root, 'assistantAttachmentMenuButton')
    attachment = find_item(root, 'assistantAttachButton')
    qtbot.waitUntil(attachment.isVisible)
    assert attachment.isEnabled()
    p = attachment.mapToScene(QPointF())
    assert host.mapToScene(QPointF()).x() <= p.x()
    assert p.x() + attachment.width() <= root.width()
    assert 0 <= p.y() and p.y() + attachment.height() < root.height() - 50
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not attachment.isVisible())
    message = find_item(root, 'assistantInput')
    # The condensed composer has a 58px minimum field (AssistantComposer.qml).
    assert message.height() >= 58
    message.setProperty('text', '让这一段更轻快，先给我试听建议。')
    assert root.grabWindow().save(str(tmp_path / f'assistant-desktop-{width}.png'))
    root.resize(1000, 640)
    app.processEvents()
    assert message.height() >= 58
    assert root.property('studioAssistantOpen') and workspace.isVisible()
    assert root.grabWindow().save(str(tmp_path / f'assistant-docked-{width}.png'))
    root.resize(width, 800)
    app.processEvents()
    click(root, 'studioAssistantConnectionSettings')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAIServicePane') is not None)
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert dialog.property('opened')
    assert not root.findChild(QObject, 'assistantSettingsDialog').property('opened')
    assert root.grabWindow().save(str(tmp_path / f'ai-service-{width}.png'))
    click(root, 'assistantAIServiceClose')
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    assert message.property('text') == '让这一段更轻快，先给我试听建议。'
    assert root.property('studioAssistantOpen')
    click(root, 'studioAssistantClose')
    assert not root.property('studioAssistantOpen')
    click(root, 'studioAssistant')
    assert message.property('text') == '让这一段更轻快，先给我试听建议。'
    assert editor.state.content == original and editor.historyUndoDepth == depth
    qml(root, 'setPage("launch"); true')
    qtbot.waitUntil(lambda: root.property('pageState') == 'launch')
    qml(root, 'setPage("editor"); true')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAssistantHost') is not None)
    assert find_item(root, 'assistantInput').property('text') == '让这一段更轻快，先给我试听建议。'


def test_service_drafts_are_guarded_and_never_cross_connections(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(),
    )
    assistant.selectProvider('openai')
    pane = open_ai_settings(root, engine, qtbot)
    key = root.findChild(QObject, 'studioAIApiKey')
    key.setProperty('text', 'test-only-credential')
    assert pane.property('dirty')
    click(root, 'studioAIConnection-deepseek')
    guard = root.findChild(QObject, 'studioAIUnsavedDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    assert assistant.currentProviderId == 'openai'
    click(root, 'studioAIKeepEditing')
    assert key.property('text') == 'test-only-credential'
    click(root, 'studioAIConnection-deepseek')
    click(root, 'studioAIDiscardChanges')
    assert assistant.currentProviderId == 'deepseek'
    assert key.property('text') == ''
    key.setProperty('text', 'second-test-credential')
    click(root, 'assistantAIServiceClose')
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIKeepEditing')
    assert root.findChild(QObject, 'assistantAIServiceDialog').property('opened')
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIDiscardChanges')
    qtbot.waitUntil(lambda: not root.findChild(QObject, 'assistantAIServiceDialog').property('opened'))


def test_service_saves_with_memory_credentials_and_retains_explicit_default(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    store = ProviderSettingsService(
        settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(),
    )
    assistant._provider_settings = store
    assistant.selectProvider('openai')
    pane = open_ai_settings(root, engine, qtbot)
    root.findChild(QObject, 'studioAIApiKey').setProperty('text', 'test-memory-key')
    root.findChild(QObject, 'studioAIModelName').setProperty('text', 'test-manual-model')
    click_item(root, reveal(root, 'studioAISaveConnection'))
    assert store.public_config('openai')['modelId'] == 'test-manual-model'
    assert pane.property('configured') and not pane.property('dirty')
    assert root.findChild(QObject, 'studioAIApiKey').property('text') == ''
    assert assistant.currentModelId == 'test-manual-model'
    assert any(x['id'] == 'test-manual-model' for x in assistant.modelCatalog)


def test_cancelled_third_party_save_does_not_leave_a_pending_close(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore())
    assistant.selectProvider('openai')
    pane = open_ai_settings(root, engine, qtbot)
    click(root, 'studioAIAddConnection')
    root.findChild(QObject, 'studioAIConnectionName').setProperty('text', 'Test custom')
    root.findChild(QObject, 'studioAIBaseUrl').setProperty('text', 'https://example.test/v1')
    root.findChild(QObject, 'studioAIApiKey').setProperty('text', 'test-custom-key')
    root.findChild(QObject, 'studioAIModelName').setProperty('text', 'test-model')
    click(root, 'assistantAIServiceClose')
    click(root, 'studioAISaveAndContinue')
    guard = root.findChild(QObject, 'studioAIThirdPartyDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not guard.property('opened'))
    dialog = root.findChild(QObject, 'assistantAIServiceDialog')
    assert dialog.property('opened') and pane.property('dirty')
    click_item(root, reveal(root, 'studioAISaveConnection'))
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIThirdPartyAccept')
    assert dialog.property('opened'), 'A cancelled close must not run after a later normal save'
    assert not pane.property('dirty')
    assert pane.property('setup')['canDelete']
    assert root.findChild(QObject, 'studioAIApiKey').property('text') == ''


def test_main_window_close_protects_unsaved_connection_form(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    pane = open_ai_settings(root, engine, qtbot)
    root.findChild(QObject, 'studioAIApiKey').setProperty('text', 'test-unsaved-key')
    assert not root.close()
    guard = root.findChild(QObject, 'studioAIUnsavedDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIKeepEditing')
    assert root.isVisible() and pane.property('dirty')
    assert not root.close()
    click(root, 'studioAIDiscardChanges')
    qtbot.waitUntil(lambda: not root.isVisible())


def test_minimap_stays_compact_and_options_allow_continuous_changes(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    root.resize(1000, 640)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    editor.createNoteAt(0, 0)
    minimap = find_item(root, 'miniMap')
    qtbot.waitUntil(minimap.isVisible)
    # The compact preview has no title; the strip only holds its two controls.
    assert not find_item(root, 'miniMapTitle').isVisible()
    assert not find_item(root, 'miniMapDimensions').isVisible()
    assert not find_item(root, 'miniMapDisplayModeButton').isVisible()
    assert root.grabWindow().save(str(tmp_path / 'minimap-compact.png'))
    click(root, 'miniMapSettingsAction')
    popup = root.findChild(QObject, 'miniMapSettingsMenu')
    qtbot.waitUntil(lambda: popup.property('opened'))
    p = qml(popup, 'background.mapToItem(null, 0, 0)')
    assert p.x() >= 0 and p.y() >= 0
    assert p.x() + popup.property('width') <= root.width()
    experience = engine.rootContext().contextProperty('experience')
    for expected in ('weak', 'visible'):
        click(root, 'studioMiniMapSoften')
        assert experience.miniMapDisplayMode == expected
        assert popup.property('opened')
    locked = experience.miniMapAspectLocked
    click(root, 'studioMiniMapAspectLock')
    assert experience.miniMapAspectLocked is not locked and popup.property('opened')
    click(root, 'studioMiniMapAspectLock')
    assert experience.miniMapAspectLocked is locked
    click(root, 'miniMapSettingsAction')
    qtbot.waitUntil(lambda: not popup.property('opened'))


def test_failed_connection_save_keeps_the_form_and_marks_the_error(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    pane = open_ai_settings(root, engine, qtbot)
    key = root.findChild(QObject, 'studioAIApiKey')
    address = root.findChild(QObject, 'studioAIBaseUrl')
    key.setProperty('text', 'test-unsaved-key')
    address.setProperty('text', 'not-a-service-address')
    click_item(root, reveal(root, 'studioAISaveConnection'))
    assert pane.property('dirty') and pane.property('saveFailed')
    assert key.property('text') == 'test-unsaved-key'
    assert address.property('text') == 'not-a-service-address'
    assert root.findChild(QObject, 'assistantAIServiceDialog').property('opened')
    assert root.findChild(QObject, 'studioAIConnectionResult').property('text')
    qml(pane, 'loadCurrent(assistant.currentProviderId); true')


def test_failed_async_probe_is_visible_in_settings_and_assistant(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')

    class Provider:
        def test_connection(self):
            raise RuntimeError('test-only failure')

    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(), provider_factory=lambda _: Provider())
    assert assistant.saveProviderSettings('openai', 'OpenAI', 'https://api.openai.com/v1',
                                          'test-memory-key', 'gpt-4o-mini', True)['success']
    pane = open_ai_settings(root, engine, qtbot)
    click_item(root, reveal(root, 'studioAITestConnection'))
    qtbot.waitUntil(lambda: not assistant.connectionTesting)
    assert assistant.connectionTested and assistant.connectionResult['code'] == 'UNKNOWN_ERROR'
    assert root.findChild(QObject, 'studioAIConnectionResult').property('text') == assistant.connectionResult['message']
    assert root.findChild(QObject, 'assistantAIServiceDialog').property('opened')
    click(root, 'assistantAIServiceClose')
    qml(root, 'openAssistantWindow(); true')
    qtbot.waitUntil(lambda: root.property('studioAssistantOpen'))
    assert '连接失败' in root.findChild(QObject, 'studioAssistantConnectionStatus').property('text')


def test_embedded_assistant_dialog_blocks_editor_shortcuts(studio_session, qtbot):
    app, engine, root, editor, playback = studio_session
    initialize_deferred_services(engine)
    click(root, 'studioCreateButton')
    qtbot.waitUntil(lambda: root.property('pageState') == 'editor')
    editor.createNoteAt(0, 0)
    editor.synchronizeNow()
    click(root, 'studioAssistant')
    button = find_item(root, 'assistantEditCreationScopeButton')
    button.forceActiveFocus()
    before, depth = editor.state.content, editor.historyUndoDepth
    click(root, 'assistantEditCreationScopeButton')
    # Scope now opens a menu first; manual bars opens the same protected modal.
    qtbot.waitUntil(lambda: root.findChild(QObject, 'assistantScopeMenu').property('opened'))
    click(root, 'assistantScopeManual')
    dialog = root.findChild(QObject, 'assistantCreationScopeDialog')
    qtbot.waitUntil(lambda: dialog.property('opened'))
    assert root.property('editorSurfaceOpen')
    assert not root.property('editorInputEnabled')
    assert not engine.rootContext().contextProperty('keyboard')._input_enabled
    QTest.keyClick(root, Qt.Key_Z, Qt.ControlModifier)
    assert editor.state.content == before and editor.historyUndoDepth == depth
    QTest.keyClick(root, Qt.Key_Escape)
    qtbot.waitUntil(lambda: not dialog.property('opened'))
    assert not root.property('editorSurfaceOpen')
