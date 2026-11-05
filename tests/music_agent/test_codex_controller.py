from threading import Event

from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.assistant_controller import MusicAssistantController
from ai_piano_studio.music_agent.codex_provider import CodexError
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ConnectionResult, ConnectionResultCode, ProviderSettingsService, map_connection_error
from ai_piano_studio.music_agent.tool_gateway import MusicAgentToolGateway
from tests.music_agent.test_assistant_controller import _editor


def assistant_at(tmp_path, provider=None):
    editor = _editor()
    service = ProviderSettingsService(settings=QSettings(str(tmp_path / 'services.ini'), QSettings.IniFormat),
        credentials=MemoryCredentialStore(), provider_factory=(lambda _id: provider) if provider else None)
    return MusicAssistantController(editor, gateway=MusicAgentToolGateway(editor), provider_settings=service)


def test_old_catalog_result_cannot_cross_a_credential_change(qapp, qtbot, tmp_path):
    started, release = Event(), Event()
    class Provider:
        def list_models(self):
            started.set()
            assert release.wait(2)
            return ['old-credential-model']
    assistant = assistant_at(tmp_path, Provider())
    assert assistant.refreshModelCatalogAsync()
    try:
        assert started.wait(0.5)
        assert assistant.saveProviderSettings('openai', '', 'https://api.openai.com/v1', 'test-memory-key', 'manual-new-model', False)['success']
    finally:
        release.set()
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    assert assistant.currentModelId == 'manual-new-model'
    assert 'old-credential-model' not in {item['id'] for item in assistant.modelCatalog}


def test_failed_sync_has_its_own_feedback_without_claiming_a_test_pass(qapp, qtbot, tmp_path):
    class Provider:
        def list_models(self):
            raise RuntimeError('private backend diagnostic')
    assistant = assistant_at(tmp_path, Provider())
    assert assistant.refreshModelCatalogAsync()
    qtbot.waitUntil(lambda: not assistant.catalogRefreshing)
    assert assistant.modelCatalogError and 'private' not in assistant.modelCatalogError
    assert not assistant.connectionTested
    assert assistant.connectionResult['code'] != 'SUCCESS'
    assistant.selectProvider('deepseek')
    assert assistant.modelCatalogError == ''


def test_repeat_detection_preserves_test_but_login_change_invalidates_it(tmp_path):
    assistant = assistant_at(tmp_path)
    assistant.selectProvider('codex')
    info = {'installed': True, 'supported': True, 'login': 'chatgpt', 'version': '0.153.4', 'message': 'logged in'}
    assistant._on_codex_detection_finished(info)
    assistant._connection_result = ConnectionResult(ConnectionResultCode.SUCCESS, 'previous successful check')
    assistant._connection_tested = True
    assistant._on_codex_detection_finished(info)
    assert assistant.connectionTested and assistant.connectionResult['code'] == 'SUCCESS'
    assistant._on_codex_detection_finished({**info, 'login': 'apikey'})
    assert not assistant.connectionTested and assistant.connectionResult['code'] == 'UNKNOWN_ERROR'
    assert not assistant.providerSetup('codex')['configured']


def test_codex_worker_observes_the_existing_controller_cancel_event(tmp_path):
    assistant = assistant_at(tmp_path)
    assistant.selectProvider('codex')
    assistant.saveProviderSettings('codex', '', '', '', 'test-cli-model', False)
    cancelled = Event()
    loop = assistant._create_agent_loop(1, cancelled)
    assert not loop._provider.is_cancelled()
    cancelled.set()
    assert loop._provider.is_cancelled()


def test_cli_authentication_feedback_retains_the_recovery_instruction():
    result = map_connection_error(CodexError('请在 Codex 中使用 ChatGPT 登录。', code='authentication'))
    assert result.code == ConnectionResultCode.INVALID_KEY
    assert 'ChatGPT' in result.message


def test_saving_tested_codex_settings_preserves_success(tmp_path):
    class Provider:
        def test_connection(self):
            pass
    assistant = assistant_at(tmp_path, Provider())
    assistant.selectProvider('codex')
    assert assistant.saveProviderSettings('codex', '', '', '', 'test-cli-model', False)['success']
    assert assistant.testConnection('codex')['code'] == 'SUCCESS'
    before = dict(assistant.connectionResult)
    assert assistant.saveProviderSettings('codex', '', '', '', 'test-cli-model', False)['success']
    assert assistant.connectionTested
    assert assistant.connectionResult == before
    assert assistant.saveProviderSettings('codex', '', '', '', 'other-cli-model', False)['success']
    assert not assistant.connectionTested
    assert assistant.connectionResult['code'] == 'UNKNOWN_ERROR'


def test_saving_tested_api_settings_preserves_result_until_endpoint_or_key_changes(tmp_path):
    class Provider:
        def test_connection(self):
            pass
    assistant = assistant_at(tmp_path, Provider())
    assert assistant.saveProviderSettings('openai', 'Name', 'https://api.openai.com/v1',
                                          'memory-test-key', 'gpt-4o-mini', False)['success']
    assert assistant.testConnection('openai')['code'] == 'SUCCESS'
    assert assistant.saveProviderSettings('openai', 'Renamed', 'https://api.openai.com/v1',
                                          '', 'gpt-4o-mini', False)['success']
    assert assistant.connectionTested and assistant.connectionResult['code'] == 'SUCCESS'
    assert assistant.saveProviderSettings('openai', 'Renamed', 'https://api.openai.com/v1',
                                          'replacement-memory-key', 'gpt-4o-mini', False)['success']
    assert not assistant.connectionTested
    assistant.testConnection('openai')
    assert assistant.saveProviderSettings('openai', 'Renamed', 'https://example.com/v1',
                                          '', 'gpt-4o-mini', True)['success']
    assert not assistant.connectionTested
