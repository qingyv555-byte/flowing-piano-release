"""Grouped service setup and CLI discovery using local, non-secret doubles."""
import json
from threading import Event

import pytest
from PySide6.QtCore import QObject, QSettings, QPointF

from ai_piano_studio.__main__ import initialize_deferred_services
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from tests.qt.test_nocturne import preserved_qsettings_state
from tests.qt.test_studio_shell import studio_session
from tests.qt.test_studio_workflows import click, click_item, find_item, qml, no_audio_device
from tests.qt.test_studio_workflow_ux import open_ai_settings, reveal


def isolated_assistant(engine, tmp_path, provider=None):
    initialize_deferred_services(engine)
    assistant = engine.rootContext().contextProperty('assistant')
    assistant._provider_settings = ProviderSettingsService(
        settings=QSettings(str(tmp_path / 'ai-services.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(),
        provider_factory=(lambda _id: provider) if provider else None)
    assistant.selectProvider('openai')
    return assistant


@pytest.mark.parametrize('width,height', [(1000, 640), (1440, 900)])
def test_api_setup_keeps_address_visible_and_models_require_sync(studio_session, qtbot, tmp_path, width, height):
    app, engine, root, editor, playback = studio_session
    assistant = isolated_assistant(engine, tmp_path)
    root.resize(width, height)
    pane = open_ai_settings(root, engine, qtbot)
    qtbot.waitUntil(lambda: not assistant.codexDetecting)
    assert not pane.property('advanced')
    assert find_item(root, 'studioAIBaseUrl').isVisible()
    assert qml(pane, 'models.length') == 0
    assert assistant.modelCatalog  # Preset names remain available to legacy consumers.
    assert not find_item(root, 'studioAIRefreshModels').isEnabled()
    assert qml(pane, 'profiles.map(function(x) { return x.heading }).filter(function(x) {return x.length > 0}).join("|")') == 'API|订阅|本地|其他 / 自定义'
    assert find_item(root, 'studioAIKeyHelp').isVisible()
    app.processEvents()
    assert root.grabWindow().save(str(tmp_path / f'ai-api-{width}.png'))
    for name in ('studioAIBaseUrl', 'studioAIModelName', 'studioAIRefreshModels'):
        item = find_item(root, name)
        point = item.mapToScene(QPointF())
        assert point.x() + item.width() <= root.width(), name
    # Repeater delegates belong to the visual tree, not the root QObject tree.
    pending, logos = [root.contentItem()], []
    while pending:
        item = pending.pop()
        if item.objectName() == 'aiBrandImage':
            logos.append(item)
        pending.extend(item.childItems())
    assert logos
    assert all(qml(logo, 'status === Image.Ready') for logo in logos)
    brands = {qml(logo.parentItem(), 'brand') for logo in logos}
    assert {'deepseek', 'anthropic', 'moonshot', 'openai', 'gemini', 'qwen'} <= brands
    assert all(logo.property('source').toString().startswith('data:image/svg+xml') for logo in logos)


def test_sync_saves_key_before_fetch_and_excludes_presets(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    calls = []
    class Provider:
        def list_models(self):
            calls.append('list')
            return ['service-returned-model']
    assistant = isolated_assistant(engine, tmp_path, Provider())
    pane = open_ai_settings(root, engine, qtbot)
    find_item(root, 'studioAIApiKey').setProperty('text', 'test-memory-key')
    find_item(root, 'studioAIModelName').setProperty('text', '')
    assert find_item(root, 'studioAIRefreshModels').isEnabled()
    click_item(root, reveal(root, 'studioAIRefreshModels'))
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    assert calls == ['list']
    assert assistant.providerSetup('openai')['credentialStatus'] == 'saved'
    assert find_item(root, 'studioAIApiKey').property('text') == ''
    assert qml(pane, 'models.map(function(x) {return x.id}).join("|")') == 'service-returned-model'
    assert not pane.property('dirty')
    app.processEvents()
    assert root.grabWindow().save(str(tmp_path / 'api-synced.png'))
    click_item(root, reveal(root, 'studioAIModel-service-returned-model'))
    assert pane.property('dirty')
    click_item(root, reveal(root, 'studioAISaveConnection'))
    assert assistant.currentModelId == 'service-returned-model'
    assert not pane.property('dirty')


def test_codex_auto_detection_is_async_and_models_remain_explicit(studio_session, qtbot, tmp_path, monkeypatch):
    app, engine, root, editor, playback = studio_session
    root.resize(1440, 900)
    started, release = Event(), Event()
    def detect(**kwargs):
        started.set()
        assert release.wait(2)
        return {'installed': True, 'version': '0.153.4', 'supported': True, 'login': 'chatgpt', 'message': '已用 ChatGPT 登录，可同步模型。'}
    monkeypatch.setattr('ai_piano_studio.music_agent.assistant_controller.detect_codex', detect)
    class Provider:
        def list_models(self):
            return ['test-codex-model', 'test-second-model']
    assistant = isolated_assistant(engine, tmp_path, Provider())
    pane = open_ai_settings(root, engine, qtbot)
    try:
        assert started.wait(0.5)
        assert assistant.codexDetecting
        assert assistant.detectCodexAsync() is False
        # The Qt event loop remains responsive while the CLI is being detected.
        root.resize(1400, 900); app.processEvents()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not assistant.codexDetecting)
    assert assistant.codexStatus['login'] == 'chatgpt'
    assert qml(pane, 'profiles[0].id') == 'codex'
    click(root, 'studioAIConnection-codex')
    assert pane.property('isCodex') and pane.property('configured')
    assert not find_item(root, 'studioAIApiKey').isVisible()
    assert not find_item(root, 'studioAIBaseUrl').isVisible()
    assert qml(pane, 'models.length') == 0
    click_item(root, reveal(root, 'studioAIRefreshModels'))
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    assert qml(pane, 'models.length') == 2
    app.processEvents()
    assert root.grabWindow().save(str(tmp_path / 'codex-synced.png'))
    click_item(root, reveal(root, 'studioAIModel-test-codex-model'))
    click_item(root, reveal(root, 'studioAISaveConnection'))
    assert assistant.providerSetup('codex')['modelId'] == 'test-codex-model'
    assert assistant.currentModelId == 'test-codex-model'
    assert assistant.currentModelStatus['canCreate'] is True
    assert 'API' not in find_item(root, 'studioAICodexStatus').property('text')
    app.processEvents()
    assert root.grabWindow().save(str(tmp_path / 'ai-codex.png'))
    persisted = (tmp_path / 'ai-services.ini').read_text(encoding='utf-8')
    assert 'test-codex-model' in persisted and 'credentialEntryId' not in persisted


def test_failed_sync_preserves_manual_model_and_credentials(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    class Provider:
        def list_models(self):
            raise RuntimeError('simulated network failure')
    assistant = isolated_assistant(engine, tmp_path, Provider())
    pane = open_ai_settings(root, engine, qtbot)
    find_item(root, 'studioAIApiKey').setProperty('text', 'test-memory-key')
    find_item(root, 'studioAIModelName').setProperty('text', 'manual-test-model')
    click_item(root, reveal(root, 'studioAIRefreshModels'))
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    assert assistant.currentModelId == 'manual-test-model'
    assert assistant.providerSetup('openai')['credentialStatus'] == 'saved'
    assert find_item(root, 'studioAIModelName').property('text') == 'manual-test-model'
    assert qml(pane, 'models.length') == 0
    assert assistant.connectionResult['code'] != 'SUCCESS'


def test_cancelled_custom_sync_cannot_trigger_a_later_unrequested_fetch(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    requests = []
    class Provider:
        def list_models(self):
            requests.append('fetch')
            return ['custom-model']
    assistant = isolated_assistant(engine, tmp_path, Provider())
    pane = open_ai_settings(root, engine, qtbot)
    click(root, 'studioAIAddConnection')
    find_item(root, 'studioAIConnectionName').setProperty('text', 'Test custom')
    find_item(root, 'studioAIBaseUrl').setProperty('text', 'https://example.test/v1')
    find_item(root, 'studioAIApiKey').setProperty('text', 'test-memory-key')
    click_item(root, reveal(root, 'studioAIRefreshModels'))
    guard = root.findChild(QObject, 'studioAIThirdPartyDialog')
    qtbot.waitUntil(lambda: guard.property('opened'))
    qml(guard, 'close(); true')
    qtbot.waitUntil(lambda: not guard.property('opened'))
    assert not pane.property('syncAfterSave') and not requests
    click_item(root, reveal(root, 'studioAISaveConnection'))
    qtbot.waitUntil(lambda: guard.property('opened'))
    click(root, 'studioAIThirdPartyAccept')
    assert assistant.currentProviderId.startswith('compatible-')
    assert not requests
    assert assistant.providerSetup(assistant.currentProviderId)['modelId'] == ''


def test_model_search_keeps_long_catalog_bounded(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    class Provider:
        def list_models(self):
            return [f'model-{i:03}' for i in range(80)]
    assistant = isolated_assistant(engine, tmp_path, Provider())
    root.resize(1000, 640)
    pane = open_ai_settings(root, engine, qtbot)
    find_item(root, 'studioAIApiKey').setProperty('text', 'test-memory-key')
    click_item(root, reveal(root, 'studioAIRefreshModels'))
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    app.processEvents()
    model_scroll = find_item(root, 'studioAIModelScroll')
    qtbot.waitUntil(lambda: 0 < model_scroll.height() <= 168)
    search = find_item(root, 'studioAIModelSearch')
    search.setProperty('text', 'model-079')
    app.processEvents()
    option = find_item(root, 'studioAIModel-model-079')
    # Qt Quick relayout follows visibility changes on the next event turn.
    qtbot.waitUntil(lambda: option.y() < 168)
    assert option.isVisible()
    assert not find_item(root, 'studioAIModel-model-001').isVisible()
    click_item(root, reveal(root, 'studioAIModel-model-079'))
    assert find_item(root, 'studioAIModelName').property('text') == 'model-079'
    assert root.grabWindow().save(str(tmp_path / 'ai-long-catalog-1000.png'))


def test_extended_providers_plans_and_sorting_keep_the_current_draft(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    assistant = isolated_assistant(engine, tmp_path)
    pane = open_ai_settings(root, engine, qtbot)
    profiles = {profile['id']: profile for profile in assistant.connectionProfiles}
    assert {'glm', 'doubao', 'minimax', 'openrouter', 'kimi-code', 'glm-coding',
        'qwen-token', 'minimax-token', 'volcengine-plan'} <= profiles.keys()
    assert qml(pane, 'profiles.filter(function(p) {return p.connectionKind === "api"}).map(function(p) {return p.id}).join("|")') == 'openai|anthropic|gemini|deepseek|kimi|glm|qwen|doubao|minimax'
    assert all(profiles[name]['connectionKind'] == 'subscription' for name in
        ('kimi-code', 'glm-coding', 'qwen-token', 'minimax-token', 'volcengine-plan'))
    field = find_item(root, 'studioAIApiKey')
    field.setProperty('text', 'test-unsaved-key')
    click(root, 'studioAISort')
    qtbot.waitUntil(lambda: root.findChild(QObject, 'studioAISortPopup').property('opened'))
    click(root, 'studioAISortName')
    assert assistant.catalogSortMode == 'name'
    assert field.property('text') == 'test-unsaved-key' and pane.property('dirty')
    click(root, 'studioAISort')
    click(root, 'studioAISortCapability')
    assert assistant.catalogSortMode == 'capability'
    assert field.property('text') == 'test-unsaved-key'


def test_synced_models_prefer_full_editions_and_allow_name_sort(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    assistant = isolated_assistant(engine, tmp_path)
    pane = open_ai_settings(root, engine, qtbot)
    assistant._provider_settings.set_dynamic_models('openai', ['gpt-5.6-luna', 'gpt-6-astra', 'gpt-5.6-sol', 'gpt-4o-mini'])
    assistant.changed.emit()
    assert qml(pane, 'models.map(function(p) {return p.id}).join("|")') == 'gpt-6-astra|gpt-5.6-sol|gpt-5.6-luna|gpt-4o-mini'
    assistant.setCatalogSortMode('name')
    assert qml(pane, 'models.map(function(p) {return p.id}).join("|")') == 'gpt-4o-mini|gpt-5.6-luna|gpt-5.6-sol|gpt-6-astra'
    assistant.setCatalogSortMode('capability')
    assistant._provider_settings.set_dynamic_models('openai', ['unknown-first', 'unknown-second', 'unknown-third'])
    assistant.changed.emit()
    assert qml(pane, 'models.map(function(p) {return p.id}).join("|")') == 'unknown-first|unknown-second|unknown-third'


def test_studio_creator_framework_is_editable_and_not_a_verification_gate(studio_session, qtbot, tmp_path):
    app, engine, root, editor, playback = studio_session
    assistant = isolated_assistant(engine, tmp_path)
    pane = open_ai_settings(root, engine, qtbot)
    assert assistant.creatorEnabled
    assert assistant.currentModelStatus['canCreate'] == bool(assistant.currentModelId)
    pane.setProperty('frameworkExpanded', True)
    app.processEvents()
    field = find_item(root, 'studioCreatorInstructions')
    assert field.isVisible()
    assert 'report_progress' in field.property('text')
    field.setProperty('text', '我们一起作曲。请按用户需要自由调整流程。')
    assistant.setCreatorInstructions(field.property('text'))
    assert assistant.creatorInstructions == field.property('text')
    assistant.setCreatorRequestOptions('{"max_tokens": 12000}')
    assert json.loads(assistant.creatorRequestOptions)['max_tokens'] == 12000
    workspace = tmp_path / 'new-creator-workspace'
    assistant.setCreatorWorkspacePath(str(workspace))
    assert workspace.is_dir()
    assert assistant.creatorWorkspacePath == str(workspace.resolve())
    assert root.grabWindow().save(str(tmp_path / 'creator-framework.png'))
