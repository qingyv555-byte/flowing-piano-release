from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.credentials import MemoryCredentialStore
from ai_piano_studio.music_agent.capabilities import ModelCapabilities
from ai_piano_studio.music_agent.provider_settings import (
    ConnectionResultCode,
    OllamaProvider,
    ProviderSettingsService,
    ThirdPartyWarningRequired,
    map_connection_error,
)
from ai_piano_studio.music_agent.providers import ProviderError


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "assistant.ini"), QSettings.Format.IniFormat)


@pytest.mark.parametrize("provider_id", ["openai", "deepseek", "anthropic", "gemini", "qwen", "kimi",
    "glm", "doubao", "minimax", "openrouter", "kimi-code", "glm-coding", "qwen-token", "minimax-token", "volcengine-plan"])
def test_provider_connections_roundtrip_without_assuming_tool_support(tmp_path, provider_id):
    from ai_piano_studio.music_agent.anthropic_provider import AnthropicProvider
    credentials = MemoryCredentialStore()
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)
    config = service.public_config(provider_id)
    service.save(provider_id, "", config["baseUrl"], "local-test-key", "manual-model", False)
    restored = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)
    assert restored.public_config(provider_id)["modelId"] == "manual-model"
    assert restored.capability_state(provider_id, "manual-model") == "unknown"
    provider = restored.create_provider(provider_id)
    assert isinstance(provider, AnthropicProvider) == (provider_id == "anthropic")
    assert not provider.get_capabilities("manual-model").supports_tools
    assert restored.requires_third_party_warning(provider_id, "https://third-party.example/v1")
    assert "local-test-key" not in _settings_text(tmp_path)


def _settings_text(tmp_path: Path) -> str:
    settings_path = tmp_path / "assistant.ini"
    return settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""


@contextmanager
def _http_server(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _TestProvider:
    def __init__(self, outcome: object = True) -> None:
        self.outcome = outcome
        self.calls = 0

    def list_models(self) -> list[str]:
        return ["safe-model"]

    def get_capabilities(self, model_id: str):
        raise AssertionError("not needed by this test double")

    def stream_chat(self, messages, tools, reasoning, attachments):
        raise AssertionError("not available before Phase 8")

    def test_connection(self) -> bool:
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return bool(self.outcome)

    def normalize_error(self, error: BaseException) -> ProviderError:
        return ProviderError("normalized")


class _CapabilityProbeProvider(_TestProvider):
    def __init__(self) -> None:
        super().__init__()
        self.probe_calls = 0

    def verify_tool_capability(self) -> ModelCapabilities:
        self.probe_calls += 1
        return ModelCapabilities(True, False, False, False, True)


def test_save_provider_keeps_api_key_only_in_credential_store(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)

    saved = service.save(
        "openai",
        "",
        "https://api.openai.com/v1",
        "phase-seven-secret",
        "gpt-4o-mini",
        False,
    )

    assert saved["providerId"] == "openai"
    assert saved["hasCredential"] is True
    assert credentials.get(service.credential_reference("openai")) == "phase-seven-secret"
    assert "phase-seven-secret" not in _settings_text(tmp_path)
    assert "credentialEntryId" in _settings_text(tmp_path)


def test_public_config_reports_a_saved_credential_without_exposing_its_value(tmp_path: Path) -> None:
    """Break caught: a stored key looks absent, or its value leaks into the public UI projection."""
    credentials = MemoryCredentialStore()
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)

    service.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "credential-never-for-qml",
        "deepseek-chat",
        False,
    )

    public = service.public_config("deepseek")

    assert public["hasCredential"] is True
    assert public["credentialStatus"] == "saved"
    assert "credential-never-for-qml" not in repr(public)


def test_create_provider_uses_a_selected_catalog_model_without_replacing_the_saved_default(
    tmp_path: Path,
) -> None:
    """Break caught: the model selector changed only the UI while transport kept the saved model."""
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    service.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "test-key",
        "deepseek-chat",
        False,
    )

    provider = service.create_provider("deepseek", model_id="deepseek-v4-flash")

    assert provider._config.model_id == "deepseek-v4-flash"
    assert service.public_config("deepseek")["modelId"] == "deepseek-chat"


def test_replacing_provider_key_reuses_opaque_credential_reference(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)
    service.save("deepseek", "", "https://api.deepseek.com/v1", "first-key", "deepseek-chat", False)
    before = service.credential_reference("deepseek")

    service.save("deepseek", "", "https://api.deepseek.com/v1", "second-key", "deepseek-chat", False)

    assert service.credential_reference("deepseek") == before
    assert credentials.get(before) == "second-key"
    assert "first-key" not in _settings_text(tmp_path)
    assert "second-key" not in _settings_text(tmp_path)


def test_public_compatible_endpoint_needs_explicit_acknowledgement(tmp_path: Path) -> None:
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())

    with pytest.raises(ThirdPartyWarningRequired):
        service.save(
            "compatible",
            "My bridge",
            "https://bridge.example.test/v1",
            "bridge-key",
            "bridge-model",
            False,
        )

    service.save(
        "compatible",
        "My bridge",
        "https://bridge.example.test/v1",
        "bridge-key",
        "bridge-model",
        True,
    )
    assert service.requires_third_party_warning("compatible", "https://bridge.example.test/v1") is False
    assert service.requires_third_party_warning("compatible", "https://other.example.test/v1") is True


def test_noncanonical_fixed_provider_endpoint_needs_acknowledgement_fingerprint(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = ProviderSettingsService(settings=settings, credentials=MemoryCredentialStore())
    endpoint = "https://openai-bridge.example.test/v1"

    with pytest.raises(ThirdPartyWarningRequired):
        service.save("openai", "", endpoint, "bridge-key", "gpt-4o-mini", False)

    service.save("openai", "", endpoint, "bridge-key", "gpt-4o-mini", True)

    assert service.requires_third_party_warning("openai", endpoint) is False
    assert service.requires_third_party_warning("openai", "https://other-bridge.example.test/v1") is True
    assert settings.contains("musicAssistant/providers/openai/acknowledgedEndpoint") is False
    fingerprint = str(settings.value("musicAssistant/providers/openai/acknowledgedEndpointFingerprint"))
    assert len(fingerprint) == 64
    assert fingerprint != endpoint


def test_loopback_http_is_allowed_but_public_http_is_rejected(tmp_path: Path) -> None:
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())

    saved = service.save("ollama", "", "http://127.0.0.1:11434", "", "llama3.2", False)

    assert saved["baseUrl"] == "http://127.0.0.1:11434"
    with pytest.raises(ValueError, match="loopback|HTTPS"):
        service.save("compatible", "Bridge", "http://bridge.example.test/v1", "key", "model", True)


def test_ollama_catalog_does_not_follow_redirects_outside_the_configured_endpoint() -> None:
    target_hits: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            target_hits.append(self.path)
            payload = json.dumps({"models": [{"name": "redirected-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    with _http_server(TargetHandler) as target_url:
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                self.send_response(302)
                self.send_header("Location", f"{target_url}/api/tags")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                del format, args

        with _http_server(RedirectHandler) as redirect_url:
            provider = OllamaProvider(redirect_url)
            with pytest.raises(ProviderError):
                provider.list_models()

    assert target_hits == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderError("provider request failed (401): Bearer phase-secret"), ConnectionResultCode.INVALID_KEY),
        (ValueError("provider URL must use HTTPS"), ConnectionResultCode.INVALID_BASE_URL),
        (URLError("network unreachable"), ConnectionResultCode.NETWORK_ERROR),
        (socket.timeout("timed out"), ConnectionResultCode.TIMEOUT),
        (ProviderError("model not found"), ConnectionResultCode.MODEL_NOT_FOUND),
        (ProviderError("selected model does not support tools"), ConnectionResultCode.UNSUPPORTED_MODEL),
        (RuntimeError("unexpected failure"), ConnectionResultCode.UNKNOWN_ERROR),
    ],
)
def test_connection_failures_are_scrubbed_and_classified(error: BaseException, expected: ConnectionResultCode) -> None:
    result = map_connection_error(error, "phase-secret")

    assert result.code is expected
    assert "phase-secret" not in result.message
    assert "[REDACTED]" not in result.message


def test_connection_uses_provider_abstraction_and_returns_safe_success(tmp_path: Path) -> None:
    provider = _TestProvider()
    service = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda provider_id: provider,
    )
    service.save("openai", "", "https://api.openai.com/v1", "connection-key", "gpt-4o-mini", False)

    result = service.test_connection("openai")

    assert provider.calls == 1
    assert result.code is ConnectionResultCode.SUCCESS
    assert "connection-key" not in result.message


def test_connection_verifies_an_unknown_selected_model_before_enabling_tools(tmp_path: Path) -> None:
    """Break caught: an ID-only provider catalog leaves a Tool-capable paid model unusable."""
    provider = _CapabilityProbeProvider()
    service = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: provider,
    )
    service.save(
        "deepseek",
        "",
        "https://api.deepseek.com/v1",
        "connection-key",
        "deepseek-v4-flash",
        False,
    )
    service.set_dynamic_models("deepseek", ["deepseek-v4-flash"])

    result = service.test_connection("deepseek")

    assert result.code is ConnectionResultCode.SUCCESS
    assert "工具调用" in result.message
    assert provider.probe_calls == 1
    assert service.capabilities_for("deepseek", "deepseek-v4-flash").supports_tools is True
    catalog = {item["id"]: item for item in service.model_catalog("deepseek")}
    assert catalog["deepseek-v4-flash"]["capabilities"]["supportsTools"] is True
    assert "工具能力未验证" not in catalog["deepseek-v4-flash"]["label"]


def test_dynamic_model_catalog_can_replace_fallback_models_without_touching_settings(tmp_path: Path) -> None:
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())

    service.set_dynamic_models("compatible", ["bridge-fast", "bridge-reasoning"])

    assert [item["id"] for item in service.model_catalog("compatible")] == [
        "bridge-fast",
        "bridge-reasoning",
    ]
    assert "bridge-fast" not in _settings_text(tmp_path)


def test_custom_compatible_profiles_persist_independently_without_storing_key_material(
    tmp_path: Path,
) -> None:
    credentials = MemoryCredentialStore()
    settings = _settings(tmp_path)
    service = ProviderSettingsService(settings=settings, credentials=credentials)

    first = service.create_custom_profile(
        "工作中转",
        "https://work-bridge.example.test/v1",
        "work-model",
        "first-profile-secret",
        third_party_acknowledged=True,
    )
    second = service.create_custom_profile(
        "备用中转",
        "https://backup-bridge.example.test/v1",
        "backup-model",
        "second-profile-secret",
        third_party_acknowledged=True,
    )

    reloaded = ProviderSettingsService(settings=settings, credentials=credentials)
    profiles = {profile["providerId"]: profile for profile in reloaded.connection_profiles()}

    assert first["providerId"] != second["providerId"]
    assert profiles[first["providerId"]]["displayName"] == "工作中转"
    assert profiles[second["providerId"]]["modelId"] == "backup-model"
    assert profiles[first["providerId"]]["providerType"] == "compatible"
    assert profiles[first["providerId"]]["credentialStatus"] == "saved"
    persisted = _settings_text(tmp_path)
    assert "profileId" in persisted
    assert "providerType=compatible" in persisted
    assert "defaultModel=work-model" in persisted
    assert "modelSource=manual" in persisted
    assert "credentialRef" in persisted
    assert "capabilityCache" in persisted
    assert "first-profile-secret" not in persisted
    assert "second-profile-secret" not in persisted


def test_custom_profile_keeps_manual_model_when_catalog_refresh_fails(tmp_path: Path) -> None:
    service = ProviderSettingsService(
        settings=_settings(tmp_path),
        credentials=MemoryCredentialStore(),
        provider_factory=lambda _provider_id: (_ for _ in ()).throw(ProviderError("refresh failed")),
    )
    profile = service.create_custom_profile(
        "测试中转",
        "https://bridge.example.test/v1",
        "manual-model",
        "bridge-secret",
        third_party_acknowledged=True,
    )

    with pytest.raises(ProviderError, match="refresh failed"):
        service.refresh_models(profile["providerId"])

    assert service.public_config(profile["providerId"])["modelId"] == "manual-model"
    assert service.public_config(profile["providerId"])["modelSource"] == "manual"


def test_only_custom_profiles_can_be_renamed_or_deleted(tmp_path: Path) -> None:
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    profile = service.create_custom_profile(
        "可删配置",
        "https://bridge.example.test/v1",
        "bridge-model",
        "bridge-secret",
        third_party_acknowledged=True,
    )

    renamed = service.rename_custom_profile(profile["providerId"], "新的名称")
    service.delete_profile(profile["providerId"])

    assert renamed["displayName"] == "新的名称"
    assert profile["providerId"] not in {item["providerId"] for item in service.connection_profiles()}
    with pytest.raises(ValueError, match="official"):
        service.delete_profile("openai")


def test_verified_dynamic_model_capability_survives_service_restart(
    tmp_path: Path,
) -> None:
    """Break caught: a verified provider model regresses to unverified after restart."""
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())

    service.set_dynamic_models("deepseek", ["provider-new-model"])

    assert service.capability_state("deepseek", "provider-new-model") == "unknown"
    assert service.capabilities_for("deepseek", "provider-new-model").supports_tools is False

    service.record_capability_verification(
        "deepseek", "provider-new-model", ModelCapabilities(True, False, False, False, True)
    )

    assert service.capability_state("deepseek", "provider-new-model") == "known_supported"
    assert service.capabilities_for("deepseek", "provider-new-model").supports_tools is True

    reloaded = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    assert reloaded.capability_state("deepseek", "provider-new-model") == "known_supported"
    assert reloaded.capabilities_for("deepseek", "provider-new-model").supports_tools is True
    assert "provider-new-model" in {item["id"] for item in reloaded.model_catalog("deepseek")}


def test_ollama_transport_binds_the_selected_catalog_model(tmp_path: Path) -> None:
    """Break caught: Ollama verification silently probes the saved default instead of the selected model."""
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=MemoryCredentialStore())
    service.save("ollama", "", "http://127.0.0.1:11434", "", "saved-model", False)

    provider = service.create_provider("ollama", model_id="selected-model")

    assert isinstance(provider, OllamaProvider)
    assert provider._model_id == "selected-model"


def test_saving_a_changed_default_model_invalidates_verified_capability_cache(tmp_path: Path) -> None:
    """Break caught: a result verified for one configured model remains trusted after settings change."""
    credentials = MemoryCredentialStore()
    service = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)
    service.save("deepseek", "", "https://api.deepseek.com/v1", "connection-key", "deepseek-v4-pro", False)
    service.record_capability_verification(
        "deepseek", "deepseek-v4-pro", ModelCapabilities(True, False, False, False, True)
    )

    service.save("deepseek", "", "https://api.deepseek.com/v1", "", "deepseek-chat", False)
    reloaded = ProviderSettingsService(settings=_settings(tmp_path), credentials=credentials)

    assert reloaded.capability_state("deepseek", "deepseek-v4-pro") == "unknown"
