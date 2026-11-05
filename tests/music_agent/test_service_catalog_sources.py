from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService


def service_at(tmp_path):
    return ProviderSettingsService(settings=QSettings(str(tmp_path / 'providers.ini'), QSettings.IniFormat), credentials=MemoryCredentialStore())


def test_service_names_separate_api_and_subscription_without_location_suffixes(tmp_path):
    service = service_at(tmp_path)
    # Previously saved default labels must not keep the old UI wording alive.
    service._settings.setValue(f'{service._prefix("qwen")}/displayName', '通义千问 · 百炼（北京）')
    service._settings.setValue(f'{service._prefix("anthropic")}/displayName', 'Claude / Anthropic')
    labels = {item['id']: item['displayName'] for item in service.connection_profiles()}
    assert labels['openai'] == 'OpenAI'
    assert labels['codex'] == 'ChatGPT'
    assert labels['anthropic'] == 'Anthropic'
    assert labels['qwen'] == '通义千问'
    assert labels['kimi'] == 'Kimi'
    assert labels['ollama'] == 'Ollama'
    assert labels['compatible'] == '自定义服务'
    assert all('API' not in label and 'Compatible' not in label for label in labels.values())
    assert all('中国' not in label and '北京' not in label for label in labels.values())


def test_presets_and_manual_models_are_distinguished_from_synced_models(tmp_path):
    service = service_at(tmp_path)
    assert all(item['source'] == 'preset' for item in service.model_catalog('openai'))
    service.save('openai', '', 'https://api.openai.com/v1', 'test-only-key', 'manual-model', False)
    service.set_dynamic_models('openai', ['discovered-model'])
    catalog = {item['id']: item for item in service.model_catalog('openai')}
    assert catalog['manual-model']['source'] == 'manual'
    assert catalog['discovered-model']['source'] == 'synced'
    assert catalog['gpt-4o-mini']['source'] == 'preset'


def test_a_changed_endpoint_cannot_reuse_an_old_synced_catalog(tmp_path):
    service = service_at(tmp_path)
    service.save('openai', '', 'https://api.openai.com/v1', 'test-only-key', 'manual-model', False)
    service.set_dynamic_models('openai', ['old-service-model'])
    service.save('openai', '', 'https://example.test/v1', '', 'manual-model', True)
    assert 'old-service-model' not in {item['id'] for item in service.model_catalog('openai')}


def test_custom_connection_can_save_a_key_before_a_model_is_known(tmp_path):
    service = service_at(tmp_path)
    profile = service.create_custom_profile('My endpoint', 'https://example.test/v1', '',
        'test-only-key', third_party_acknowledged=True)
    assert profile['configured'] and profile['modelId'] == ''
    assert service.model_catalog(profile['providerId']) == []
    service.set_dynamic_models(profile['providerId'], ['discovered-model'])
    assert service.model_catalog(profile['providerId'])[0]['source'] == 'synced'


def test_codex_is_separate_from_api_credentials_and_does_not_change_them(tmp_path):
    service = service_at(tmp_path)
    service.save('openai', '', 'https://api.openai.com/v1', 'test-only-key', 'manual-model', False)
    assert not service.public_config('codex')['configured']
    service.update_codex_status({'installed': True, 'version': 'test', 'login': 'apikey', 'supported': True})
    assert not service.public_config('codex')['configured']
    service.update_codex_status({'installed': True, 'version': 'test', 'login': 'chatgpt', 'supported': True})
    service.save('codex', '', '', '', 'cli-returned-model', False)
    assert service.public_config('codex')['configured']
    assert service.public_config('openai')['modelId'] == 'manual-model'
    assert service.public_config('openai')['credentialStatus'] == 'saved'
    kinds = {item['id']: item['connectionKind'] for item in service.connection_profiles()}
    assert kinds['codex'] == 'subscription' and kinds['openai'] == 'api'
    assert kinds['ollama'] == 'local' and kinds['compatible'] == 'custom'
