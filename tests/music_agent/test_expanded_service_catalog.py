"""New providers and plans use the real transport against a loopback stub only."""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.catalog_ranking import model_preference, provider_priority
from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.provider_settings import ProviderSettingsService
from tests.music_agent.test_provider_settings import _http_server
from tests.music_agent.test_agent_loop import _sse_tool_call


NEW_IDS = ['glm', 'doubao', 'minimax', 'openrouter', 'kimi-code', 'glm-coding',
           'qwen-token', 'minimax-token', 'volcengine-plan']


def make_service(tmp_path, credentials=None):
    return ProviderSettingsService(settings=QSettings(str(tmp_path / 'catalog.ini'), QSettings.IniFormat),
        credentials=credentials or MemoryCredentialStore())


@pytest.mark.parametrize('provider_id', NEW_IDS)
def test_each_new_connection_preserves_its_base_path_and_authentication(tmp_path, provider_id):
    captured = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return
        def do_GET(self):
            captured.append(('GET', self.path, self.headers.get('Authorization')))
            body = json.dumps({'data': [{'id': 'server-returned-model'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(('POST', self.path, self.headers.get('Authorization'), body))
            stream = (_sse_tool_call('probe', 'music_capability_probe', {})
                if body.get('tools') else
                b'data: {"choices":[{"delta":{"content":"local response"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(stream)))
            self.end_headers(); self.wfile.write(stream)
    service = make_service(tmp_path)
    official = service.public_config(provider_id)
    path = urlsplit(official['baseUrl']).path
    key = 'test-memory-' + provider_id
    with _http_server(Handler) as server:
        service.save(provider_id, '', server + path, key, 'manual-model', True)
        provider = service.create_provider(provider_id)
        assert provider.list_models() == ['server-returned-model']
        assert service.capability_state(provider_id, 'manual-model') == 'unknown'
        assert service.verify_music_tools(provider_id, 'manual-model').code.value == 'SUCCESS'
        provider = service.create_provider(provider_id)
        assert ''.join(event.text_delta for event in provider.stream_events(
            [{'role': 'user', 'content': 'local transport check'}], [], None, ())) == 'local response'
    assert captured[0] == ('GET', path + '/models', 'Bearer ' + key)
    assert captured[1][:3] == ('POST', path + '/chat/completions', 'Bearer ' + key)
    assert captured[1][3]['model'] == 'manual-model'
    assert captured[2][:3] == ('POST', path + '/chat/completions', 'Bearer ' + key)
    assert service.capability_state(provider_id, 'manual-model') == 'known_supported'
    assert key not in (tmp_path / 'catalog.ini').read_text(encoding='utf-8')


@pytest.mark.parametrize('api_id,plan_id', [('kimi', 'kimi-code'), ('glm', 'glm-coding'),
    ('qwen', 'qwen-token'), ('minimax', 'minimax-token'), ('doubao', 'volcengine-plan')])
def test_plan_credentials_and_defaults_are_independent_of_pay_as_you_go(tmp_path, api_id, plan_id):
    credentials = MemoryCredentialStore()
    service = make_service(tmp_path, credentials)
    api_config, plan_config = service.public_config(api_id), service.public_config(plan_id)
    service.save(api_id, '', api_config['baseUrl'], 'test-api-key', 'api-model', False)
    service.save(plan_id, '', plan_config['baseUrl'], 'test-plan-key', 'plan-model', False)
    assert service.credential_reference(api_id) != service.credential_reference(plan_id)
    assert credentials.get(service.credential_reference(api_id)) == 'test-api-key'
    assert credentials.get(service.credential_reference(plan_id)) == 'test-plan-key'
    assert service.public_config(api_id)['modelId'] == 'api-model'
    assert service.public_config(plan_id)['modelId'] == 'plan-model'
    profiles = {profile['id']: profile for profile in service.connection_profiles()}
    assert profiles[plan_id]['connectionKind'] == 'subscription'
    assert profiles[plan_id]['brandId'] != plan_id


def test_catalog_defaults_and_model_recommendations_do_not_grant_capabilities(tmp_path):
    service = make_service(tmp_path)
    models = ['glm-5.3-flash', 'glm-5.2', 'glm-5.3']
    service.set_dynamic_models('glm', models)
    catalog = service.model_catalog('glm')
    assert [item['id'] for item in sorted(catalog, key=lambda item: item['recommendationPriority'])] == [
        'glm-5.3', 'glm-5.2', 'glm-5.3-flash']
    assert all(item['capabilityState'] == 'unknown' and not item['capabilities']['supportsTools'] for item in catalog)
    assert model_preference('unknown-new-model') == model_preference('another-unknown-model')
    assert provider_priority('codex') == provider_priority('openai')
    assert provider_priority('glm-coding') == provider_priority('glm')


def test_catalog_order_preference_persists_without_changing_connection_settings(tmp_path):
    service = make_service(tmp_path)
    assert service.catalog_sort_mode == 'capability'
    assert service.set_catalog_sort_mode('name')
    assert make_service(tmp_path).catalog_sort_mode == 'name'
    assert not service.set_catalog_sort_mode('invented')
    assert service.catalog_sort_mode == 'name'
