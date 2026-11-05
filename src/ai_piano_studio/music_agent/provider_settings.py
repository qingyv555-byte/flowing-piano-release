"""Secure, presentation-ready provider configuration for the Assistant UI.

This module deliberately stops at catalog/configuration/connection testing. It
does not start an assistant conversation or assemble music-agent context; that
belongs to the later Phase 8 runtime.
"""

from __future__ import annotations

import hashlib
import json
import socket
from uuid import uuid4
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.capabilities import ModelCapabilities, ReasoningOption
from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.anthropic_provider import AnthropicProvider
from ai_piano_studio.music_agent.codex_provider import CODEX_CAPABILITIES, CodexError, CodexProvider
from ai_piano_studio.music_agent.catalog_ranking import model_preference, provider_priority
from ai_piano_studio.music_agent.credentials import (
    CredentialNotFoundError,
    CredentialReference,
    CredentialStore,
)
from ai_piano_studio.music_agent.providers import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    DeepSeekProvider,
    ModelProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderError,
)
from ai_piano_studio.music_agent.security import (
    ProviderEndpoint,
    SecretScrubber,
    validate_provider_endpoint,
)


SETTINGS_ROOT = "musicAssistant/providers"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_CONSERVATIVE_CAPABILITIES = ModelCapabilities(False, False, False, False, False)
_UNKNOWN_CAPABILITIES = ModelCapabilities(False, False, False, False, True)
_CUSTOM_PROFILE_ROOT = f"{SETTINGS_ROOT}/customProfiles"
_CAPABILITY_CACHE_VERSION = 1
_OLLAMA_CAPABILITIES = ModelCapabilities(
    False,
    False,
    False,
    False,
    False,
    (
        ReasoningOption("low", "低", "尽量少使用推理。"),
        ReasoningOption("medium", "标准", "使用标准推理。"),
        ReasoningOption("high", "高", "使用更多推理。"),
    ),
)


class ConnectionResultCode(StrEnum):
    SUCCESS = "SUCCESS"
    INVALID_KEY = "INVALID_KEY"
    INVALID_BASE_URL = "INVALID_BASE_URL"
    NETWORK_ERROR = "NETWORK_ERROR"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    code: ConnectionResultCode
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}


class ThirdPartyWarningRequired(ValueError):
    """Raised before a public OpenAI-compatible endpoint is first persisted."""


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider_id: str
    label: str
    default_base_url: str
    fallback_models: tuple[str, ...]
    requires_api_key: bool
    supports_display_name: bool
    documentation_url: str
    setup_steps: tuple[str, ...]
    capabilities: Mapping[str, ModelCapabilities]
    connection_kind: str = "api"
    brand_id: str = ""
    connection_notice: str = ""

    def to_catalog_dict(self) -> dict[str, object]:
        return {
            "id": self.provider_id,
            "label": self.label,
            "defaultBaseUrl": self.default_base_url,
            "requiresApiKey": self.requires_api_key,
            "supportsDisplayName": self.supports_display_name,
            "documentationUrl": self.documentation_url,
            "setupSteps": list(self.setup_steps),
            "connectionNotice": self.connection_notice,
        }


_PROVIDER_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        ("gpt-4o-mini", "gpt-4o", "o3"),
        True,
        False,
        "https://platform.openai.com/api-keys",
        (
            "在 OpenAI 官网注册或登录。",
            "在 API Keys 页面创建新的 API Key。",
            "将 Key 粘贴到下方输入框。",
            "确认 Base URL 和模型后，点击测试连接。",
        ),
        OpenAIProvider.CAPABILITIES,
    ),
    ProviderDefinition(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        ("deepseek-chat", "deepseek-reasoner"),
        True,
        False,
        "https://platform.deepseek.com/api_keys",
        (
            "在 DeepSeek 平台注册或登录。",
            "在 API Keys 页面创建 API Key。",
            "将 Key 粘贴到下方输入框。",
            "确认 Base URL 和模型后，点击测试连接。",
        ),
        DeepSeekProvider.CAPABILITIES,
    ),
    *(
        ProviderDefinition(
            provider_id, label, base_url, (), True, False, documentation_url,
            ("在服务商平台创建 API Key。", "保存连接后刷新模型，或填写平台提供的模型 ID。",
             "测试连接并验证音乐工具能力后开始使用。"), {},
        )
        for provider_id, label, base_url, documentation_url in (
            ("anthropic", "Anthropic", "https://api.anthropic.com/v1", "https://platform.claude.com/settings/keys"),
            ("gemini", "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "https://aistudio.google.com/api-keys"),
            ("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1", "https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key"),
            ("kimi", "Kimi", "https://api.moonshot.cn/v1", "https://platform.kimi.com/console/api-keys"),
        )
    ),
    ProviderDefinition(
        "compatible",
        "自定义服务",
        "https://",
        (),
        True,
        True,
        "",
        (
            "确认第三方服务商提供 OpenAI Compatible API。",
            "从服务商创建 API Key，并粘贴到下方输入框。",
            "填写服务商给出的 HTTPS Base URL 与模型 ID。",
            "阅读第三方提示后保存，再点击测试连接。",
        ),
        {},
    ),
    *(
        ProviderDefinition(provider_id, label, base_url, (), True, False, key_url,
            ("在服务平台创建密钥。", "保存后同步模型，也可手动填写模型名称。", "验证模型的音乐创作能力后使用。"), {},
            connection_kind=kind, brand_id=brand,
            connection_notice="使用套餐专属密钥，与按量服务分别保存。接入范围以套餐说明为准。" if kind == "subscription" else
                "填写已开通的模型 ID 或推理接入点 ID。" if provider_id == "doubao" else "")
        for provider_id, label, base_url, key_url, kind, brand in (
            ("glm", "智谱", "https://open.bigmodel.cn/api/paas/v4", "https://bigmodel.cn/usercenter/proj-mgmt/apikeys", "api", "glm"),
            ("doubao", "豆包", "https://ark.cn-beijing.volces.com/api/v3", "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey", "api", "doubao"),
            ("minimax", "MiniMax", "https://api.minimaxi.com/v1", "https://platform.minimaxi.com/user-center/basic-information/interface-key", "api", "minimax"),
            ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", "custom", "openrouter"),
            ("kimi-code", "Kimi Code", "https://api.kimi.com/coding/v1", "https://www.kimi.com/code/console", "subscription", "kimi"),
            ("glm-coding", "智谱 Coding Plan", "https://open.bigmodel.cn/api/coding/paas/v4", "https://bigmodel.cn/usercenter/proj-mgmt/apikeys", "subscription", "glm"),
            ("qwen-token", "百炼 Token Plan", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "https://bailian.console.aliyun.com/cn-beijing/subscription/overview", "subscription", "qwen"),
            ("minimax-token", "MiniMax Token Plan", "https://api.minimaxi.com/v1", "https://platform.minimaxi.com/user-center/payment/token-plan", "subscription", "minimax"),
            ("volcengine-plan", "方舟 Agent Plan", "https://ark.cn-beijing.volces.com/api/plan/v3", "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey", "subscription", "volcengine"),
        )
    ),
    ProviderDefinition(
        "codex", "ChatGPT", "", (), False, False,
        "https://learn.chatgpt.com/docs/cli", ("安装 Codex CLI 并使用 ChatGPT 登录。", "检测本机登录后同步模型。"), {},
    ),
    ProviderDefinition(
        "ollama",
        "Ollama",
        "http://127.0.0.1:11434",
        ("llama3.2", "qwen2.5"),
        False,
        False,
        "https://ollama.com/download",
        (
            "安装并启动 Ollama。",
            "确认本机服务地址（默认 http://127.0.0.1:11434）。",
            "填写已下载的模型名称。",
            "无需 API Key，点击测试连接。",
        ),
        {"llama3.2": _OLLAMA_CAPABILITIES, "qwen2.5": _OLLAMA_CAPABILITIES},
    ),
)
_DEFINITIONS_BY_ID = {definition.provider_id: definition for definition in _PROVIDER_DEFINITIONS}


class _ProviderFactory(Protocol):
    def __call__(self, provider_id: str) -> ModelProvider: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> Request | None:
        return None


class OllamaProvider:
    """No-auth local provider adapter used for catalog and connection testing only."""

    def __init__(
        self,
        base_url: str,
        *,
        model_id: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        capabilities: Mapping[str, ModelCapabilities] | None = None,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._endpoint = validate_provider_endpoint(base_url, allow_local_http=True)
        self._model_id = model_id
        self._timeout_seconds = float(timeout_seconds)
        self._capabilities = dict(capabilities or {})
        self._opener = build_opener(_NoRedirect())

    def list_models(self) -> list[str]:
        request = Request(self._url_for("/api/tags"), method="GET")
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except BaseException as error:
            raise self.normalize_error(error) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("local provider returned an oversized response")
        try:
            payload = json.loads(raw)
            models = payload["models"]
            return [item["name"] for item in models if isinstance(item, dict) and isinstance(item.get("name"), str)]
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ProviderError("local provider returned an invalid model list") from None

    def get_capabilities(self, model_id: str) -> ModelCapabilities:
        if not isinstance(model_id, str) or not model_id:
            raise ProviderError("model id is required")
        return self._capabilities.get(model_id, _CONSERVATIVE_CAPABILITIES)

    def stream_chat(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        reasoning: str | None,
        attachments: Sequence[object],
    ) -> Iterator[str]:
        del messages, tools, reasoning, attachments
        raise ProviderError("local chat is not available until Phase 8")

    def test_connection(self) -> bool:
        self.list_models()
        return True

    def normalize_error(self, error: BaseException) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        if isinstance(error, (socket.timeout, TimeoutError)):
            return ProviderError("local provider request timed out")
        if isinstance(error, HTTPError):
            return ProviderError(f"local provider request failed ({error.code})")
        if isinstance(error, URLError):
            return ProviderError("local provider request failed")
        return ProviderError("local provider request failed")

    def _url_for(self, path: str) -> str:
        endpoint = self._endpoint
        netloc = endpoint.host if endpoint.port is None else f"{endpoint.host}:{endpoint.port}"
        return urlunsplit((endpoint.scheme, netloc, f"{endpoint.base_path}{path}", "", ""))


class ProviderSettingsService:
    """The only UI configuration route for provider metadata and credentials."""

    def __init__(
        self,
        *,
        settings: QSettings,
        credentials: CredentialStore,
        provider_factory: _ProviderFactory | None = None,
    ) -> None:
        if not isinstance(settings, QSettings):
            raise ValueError("settings must be QSettings")
        self._settings = settings
        self._credentials = credentials
        self._provider_factory = provider_factory
        self._dynamic_models: dict[str, tuple[str, ...]] = {}
        self._codex_status: dict[str, object] = {}
        self._verified_capabilities: dict[tuple[str, str], ModelCapabilities] = {}
        self._capability_cache_loaded: set[str] = set()

    @property
    def definitions(self) -> tuple[ProviderDefinition, ...]:
        return _PROVIDER_DEFINITIONS

    def provider_catalog(self) -> list[dict[str, object]]:
        return [definition.to_catalog_dict() for definition in self.definitions]

    @property
    def catalog_sort_mode(self) -> str:
        value = str(self._settings.value(f"{SETTINGS_ROOT}/catalogSortMode", "capability"))
        return value if value in {"capability", "name"} else "capability"

    def set_catalog_sort_mode(self, mode: str) -> bool:
        if mode not in {"capability", "name"}:
            return False
        self._settings.setValue(f"{SETTINGS_ROOT}/catalogSortMode", mode)
        self._settings.sync()
        return True

    def connection_profiles(self) -> list[dict[str, object]]:
        """Return safe settings projections for official and user-created connections."""

        profile_ids = (*_DEFINITIONS_BY_ID, *self._custom_profile_ids())
        return [
            {"id": provider_id, **self.public_config(provider_id)}
            for provider_id in profile_ids
        ]

    def update_codex_status(self, status: Mapping[str, object]) -> None:
        """Accept only non-secret CLI detection fields, owned by the UI thread."""
        self._codex_status = {key: status.get(key) for key in ("installed", "version", "login", "supported", "message")}

    def create_custom_profile(
        self,
        display_name: str,
        base_url: str,
        model_id: str,
        api_key: str,
        *,
        third_party_acknowledged: bool,
    ) -> dict[str, object]:
        """Create one independently persisted OpenAI-compatible connection."""

        profile_id = f"compatible-{uuid4().hex}"
        self._write_custom_profile_metadata(
            profile_id,
            display_name=display_name,
            model_id=model_id,
            model_source="manual",
        )
        try:
            return self.save(
                profile_id,
                display_name,
                base_url,
                api_key,
                model_id,
                third_party_acknowledged,
            )
        except BaseException:
            self._settings.remove(self._prefix(profile_id))
            self._settings.sync()
            raise

    def rename_custom_profile(self, provider_id: str, display_name: str) -> dict[str, object]:
        self._require_custom_profile(provider_id)
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("profile display name is required")
        self._settings.setValue(f"{self._prefix(provider_id)}/displayName", display_name.strip())
        self._settings.sync()
        return self.public_config(provider_id)

    def delete_profile(self, provider_id: str) -> None:
        self._require_custom_profile(provider_id)
        config = self._read_credential_config(provider_id)
        if config is not None:
            self._credentials.delete(config.credential_reference)
        self._settings.remove(self._prefix(provider_id))
        self._settings.sync()
        self._dynamic_models.pop(provider_id, None)
        self._clear_verified_capabilities(provider_id, persist_empty_cache=False)

    def clear_saved_credentials(self) -> None:
        """Delete stored secrets while retaining provider connections/models."""
        profile_ids = (*_DEFINITIONS_BY_ID, *self._custom_profile_ids())
        for provider_id in profile_ids:
            config = self._read_credential_config(provider_id)
            if config is not None:
                try:
                    self._credentials.delete(config.credential_reference)
                except Exception:
                    logger.exception("Could not delete saved credential for %s", provider_id)
            prefix = self._prefix(provider_id)
            self._settings.remove(f"{prefix}/credentialProviderId")
            self._settings.remove(f"{prefix}/credentialEntryId")
            self._settings.remove(f"{prefix}/credentialRef")
            self._settings.remove(f"{prefix}/providerId")
        self._settings.sync()

    def model_catalog(self, provider_id: str) -> list[dict[str, object]]:
        definition = self._definition(provider_id)
        self._load_cached_capabilities(provider_id)
        discovered = self._dynamic_models.get(provider_id, definition.fallback_models)
        configured_model = str(self.public_config(provider_id)["modelId"])
        restored_models = tuple(
            model_id
            for configured_provider_id, model_id in self._verified_capabilities
            if configured_provider_id == provider_id
        )
        model_ids = tuple(
            dict.fromkeys((*discovered, *restored_models, configured_model, *definition.fallback_models))
        )
        return [
            {
                "id": model_id,
                "label": self._model_label(provider_id, model_id),
                "capabilities": self.capabilities_for(provider_id, model_id).to_dict(),
                "capabilityState": self.capability_state(provider_id, model_id),
                "recommendationPriority": model_preference(model_id),
                "source": "synced" if model_id in self._dynamic_models.get(provider_id, ()) else
                    "manual" if model_id == configured_model and self.public_config(provider_id)["configured"] else "preset",
            }
            for model_id in model_ids
            if model_id
        ]

    def _model_label(self, provider_id: str, model_id: str) -> str:
        return model_id

    def capabilities_for(self, provider_id: str, model_id: str) -> ModelCapabilities:
        if provider_id == "codex":
            return CODEX_CAPABILITIES
        definition = self._definition(provider_id)
        self._load_cached_capabilities(provider_id)
        return self._verified_capabilities.get(
            (provider_id, model_id),
            definition.capabilities.get(model_id, _UNKNOWN_CAPABILITIES),
        )

    def capability_state(self, provider_id: str, model_id: str) -> str:
        """Return a UI-safe tool capability state without guessing from model names."""

        if provider_id == "codex":
            return "known_supported" if model_id else "unknown"
        definition = self._definition(provider_id)
        self._load_cached_capabilities(provider_id)
        if (provider_id, model_id) in self._verified_capabilities:
            return "known_supported" if self._verified_capabilities[(provider_id, model_id)].supports_tools else "known_unsupported"
        known = definition.capabilities.get(model_id)
        if known is None:
            return "unknown"
        return "known_supported" if known.supports_tools else "known_unsupported"

    def record_capability_verification(
        self, provider_id: str, model_id: str, capabilities: ModelCapabilities
    ) -> None:
        """Store a non-secret verification result for this unchanged provider configuration."""

        self._definition(provider_id)
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model id is required")
        if not isinstance(capabilities, ModelCapabilities):
            raise ValueError("capabilities must be ModelCapabilities")
        self._verified_capabilities[(provider_id, model_id)] = capabilities
        self._capability_cache_loaded.add(provider_id)
        self._write_cached_capabilities(provider_id)

    def declare_image_input(self, provider_id: str, model_id: str, supported: bool) -> None:
        """User-declared image input, separately gated from verified music tools."""
        from dataclasses import replace
        if type(supported) is not bool or provider_id == 'anthropic':
            raise ValueError('此连接尚未接入图片编码')
        if self.capability_state(provider_id, model_id) != 'known_supported':
            raise ValueError('请先验证当前模型的音乐工具能力')
        capabilities = replace(self.capabilities_for(provider_id, model_id), supports_images=supported)
        self.record_capability_verification(provider_id, model_id, capabilities)

    def set_dynamic_models(self, provider_id: str, model_ids: Sequence[str]) -> None:
        """Install a process-local catalog returned by a provider; never persist it."""

        self._definition(provider_id)
        if not isinstance(model_ids, Sequence) or isinstance(model_ids, (str, bytes)):
            raise ValueError("model ids must be a sequence of strings")
        normalized = tuple(
            dict.fromkeys(
                model_id.strip()
                for model_id in model_ids
                if isinstance(model_id, str) and model_id.strip()
            )
        )
        if not normalized:
            raise ValueError("model ids must include at least one non-empty id")
        self._dynamic_models[provider_id] = normalized

    def refresh_models(self, provider_id: str) -> list[dict[str, object]]:
        """Fetch a bounded provider catalog for a later explicit UI refresh action."""

        self.set_dynamic_models(provider_id, self.create_provider(provider_id).list_models())
        if self._is_custom_profile(provider_id):
            self._settings.setValue(f"{self._prefix(provider_id)}/modelSource", "catalog")
            self._settings.sync()
        return self.model_catalog(provider_id)

    def public_config(self, provider_id: str) -> dict[str, object]:
        definition = self._definition(provider_id)
        custom = self._is_custom_profile(provider_id)
        if provider_id == "codex":
            return {
                "providerId": provider_id, "displayName": definition.label, "baseUrl": "",
                "modelId": str(self._settings.value(f"{self._prefix(provider_id)}/modelId", "")),
                "hasCredential": False, "credentialStatus": "not_required",
                "configured": self._codex_status.get("login") == "chatgpt" and self._codex_status.get("supported") is True,
                **self._profile_metadata(provider_id, definition, custom),
            }
        if definition.requires_api_key:
            config = self._read_credential_config(provider_id)
            if config is None:
                return {
                    "providerId": provider_id,
                    "displayName": self._read_display_name(provider_id, definition.label),
                    "baseUrl": definition.default_base_url,
                    "modelId": definition.fallback_models[0] if definition.fallback_models else "",
                    "hasCredential": False,
                    "credentialStatus": "missing",
                    "configured": False,
                    **self._profile_metadata(provider_id, definition, custom),
                }
            return {
                "providerId": provider_id,
                "displayName": self._read_display_name(provider_id, definition.label),
                "baseUrl": config.base_url,
                "modelId": config.model_id,
                "hasCredential": True,
                "credentialStatus": "saved",
                "configured": True,
                **self._profile_metadata(provider_id, definition, custom),
            }
        self._settings.beginGroup(self._prefix(provider_id))
        try:
            base_url = str(self._settings.value("baseUrl", definition.default_base_url))
            model_id = str(self._settings.value("modelId", definition.fallback_models[0]))
            configured = self._settings.contains("baseUrl")
        finally:
            self._settings.endGroup()
        return {
            "providerId": provider_id,
            "displayName": definition.label,
            "baseUrl": base_url,
            "modelId": model_id,
            "hasCredential": False,
            "credentialStatus": "not_required",
            "configured": configured,
            **self._profile_metadata(provider_id, definition, custom),
        }

    def credential_reference(self, provider_id: str) -> CredentialReference:
        config = self._read_credential_config(provider_id)
        if config is None:
            raise ValueError("provider has no configured credential")
        return config.credential_reference

    def requires_third_party_warning(self, provider_id: str, base_url: str) -> bool:
        if provider_id == "codex":
            return False
        endpoint = validate_provider_endpoint(base_url, allow_local_http=True)
        if not self._is_third_party_endpoint(provider_id, endpoint):
            return False
        self._settings.beginGroup(self._prefix(provider_id))
        try:
            accepted_fingerprint = str(
                self._settings.value("acknowledgedEndpointFingerprint", "")
            )
        finally:
            self._settings.endGroup()
        return accepted_fingerprint != self._endpoint_fingerprint(endpoint)

    def save(
        self,
        provider_id: str,
        display_name: str,
        base_url: str,
        api_key: str,
        model_id: str,
        third_party_acknowledged: bool,
    ) -> dict[str, object]:
        definition = self._definition(provider_id)
        if provider_id == "codex":
            if api_key or not isinstance(model_id, str):
                raise ValueError("Codex uses its own ChatGPT login, not an API key")
            self._settings.setValue(f"{self._prefix(provider_id)}/modelId", model_id.strip())
            self._settings.sync()
            return self.public_config(provider_id)
        endpoint = validate_provider_endpoint(base_url, allow_local_http=True)
        previous_config = self.public_config(provider_id)
        previous_endpoint = str(previous_config["baseUrl"])
        previous_model_id = str(previous_config["modelId"])
        is_third_party_endpoint = self._is_third_party_endpoint(provider_id, endpoint)
        if not isinstance(display_name, str) or not isinstance(api_key, str) or not isinstance(model_id, str):
            raise ValueError("provider settings must be text")
        if definition.requires_api_key:
            if self.requires_third_party_warning(provider_id, endpoint.url) and not third_party_acknowledged:
                raise ThirdPartyWarningRequired("third-party acknowledgement is required")
            existing = self._read_credential_config(provider_id)
            reference = existing.credential_reference if existing is not None else None
            if api_key:
                if reference is None:
                    reference = self._credentials.put(provider_id, api_key)
                else:
                    self._credentials.replace(reference, api_key)
            if reference is None:
                raise ValueError("API Key is required")
            config = ProviderConfig(
                provider_id=provider_id,
                base_url=endpoint.url,
                credential_reference=reference,
                model_id=model_id.strip(),
                allow_local_http=endpoint.scheme == "http",
            )
            config.write_to_settings(self._settings, self._prefix(provider_id))
            if self._is_custom_profile(provider_id):
                self._settings.setValue(
                    f"{self._prefix(provider_id)}/credentialRef", reference.entry_id
                )
        else:
            if api_key:
                raise ValueError("Local / Ollama does not use an API Key")
            self._settings.beginGroup(self._prefix(provider_id))
            try:
                self._settings.setValue("baseUrl", endpoint.url)
                self._settings.setValue("modelId", model_id.strip())
            finally:
                self._settings.endGroup()
        self._settings.beginGroup(self._prefix(provider_id))
        try:
            if definition.supports_display_name:
                self._settings.setValue("displayName", display_name.strip() or definition.label)
            self._settings.remove("acknowledgedEndpoint")
            if third_party_acknowledged and is_third_party_endpoint:
                self._settings.setValue(
                    "acknowledgedEndpointFingerprint", self._endpoint_fingerprint(endpoint)
                )
        finally:
            self._settings.endGroup()
        self._settings.sync()
        if api_key or previous_endpoint != endpoint.url:
            self._dynamic_models.pop(provider_id, None)
        if api_key or previous_endpoint != endpoint.url or previous_model_id != model_id.strip():
            self._clear_verified_capabilities(provider_id)
        return self.public_config(provider_id)

    def create_provider(self, provider_id: str, *, model_id: str | None = None) -> ModelProvider:
        """Create a transport bound to the active catalog choice without persisting that choice."""
        self._definition(provider_id)
        if self._provider_factory is not None:
            return self._provider_factory(provider_id)
        if provider_id == "codex":
            return CodexProvider(model_id if model_id is not None else str(self.public_config(provider_id)["modelId"]))
        if provider_id == "ollama":
            config = self.public_config(provider_id)
            selected_model_id = model_id.strip() if isinstance(model_id, str) and model_id.strip() else str(config["modelId"])
            return OllamaProvider(
                str(config["baseUrl"]),
                model_id=selected_model_id,
                capabilities=_DEFINITIONS_BY_ID[provider_id].capabilities,
            )
        config = self._read_credential_config(provider_id)
        if config is None:
            raise ValueError("Please save an API Key first")
        if model_id is not None:
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("model id is required")
            config = replace(config, model_id=model_id.strip())
        if provider_id == "openai":
            return OpenAIProvider(
                config, self._credentials, model_capabilities=self._capability_overrides(provider_id)
            )
        if provider_id == "deepseek":
            return DeepSeekProvider(
                config, self._credentials, model_capabilities=self._capability_overrides(provider_id)
            )
        if provider_id == "anthropic":
            return AnthropicProvider(
                config, self._credentials, model_capabilities=self._capability_overrides(provider_id)
            )
        return OpenAICompatibleProvider(
            config, self._credentials, model_capabilities=self._capability_overrides(provider_id)
        )

    def test_connection(self, provider_id: str, model_id: str | None = None) -> ConnectionResult:
        selected_model_id = model_id.strip() if isinstance(model_id, str) and model_id.strip() else str(
            self.public_config(provider_id)["modelId"]
        )
        try:
            provider = self.create_provider(provider_id, model_id=selected_model_id)
            provider.test_connection()
            if self._requires_tool_capability_verification(provider_id, selected_model_id):
                verifier = getattr(provider, "verify_tool_capability", None)
                if not callable(verifier):
                    raise ProviderError("selected model does not support streaming tool calls")
                capabilities = verifier()
                if not isinstance(capabilities, ModelCapabilities):
                    raise ProviderError("selected model does not support streaming tool calls")
                self.record_capability_verification(provider_id, selected_model_id, capabilities)
        except BaseException as error:
            return map_connection_error(error)
        if (provider_id, selected_model_id) in self._verified_capabilities:
            capabilities = self._verified_capabilities[(provider_id, selected_model_id)]
            if not capabilities.supports_tools:
                return ConnectionResult(
                    ConnectionResultCode.SUCCESS,
                    "连接成功；已确认该模型不支持音乐创作工具，仍可聊天。",
                )
            return ConnectionResult(
                ConnectionResultCode.SUCCESS, "连接成功，已验证该模型支持流式音乐工具调用。"
            )
        return ConnectionResult(ConnectionResultCode.SUCCESS, "连接成功，可以开始使用。")

    def verify_music_tools(self, provider_id: str, model_id: str) -> ConnectionResult:
        """Probe only the currently selected model, avoiding an unrelated catalog request."""
        try:
            provider = self.create_provider(provider_id, model_id=model_id)
            verifier = getattr(provider, "verify_tool_capability", None)
            if not callable(verifier):
                raise ProviderError("selected model does not support streaming tool calls")
            capabilities = verifier()
            if not isinstance(capabilities, ModelCapabilities):
                raise ProviderError("selected model does not support streaming tool calls")
            self.record_capability_verification(provider_id, model_id, capabilities)
        except BaseException as error:
            return map_connection_error(error)
        if not capabilities.supports_tools:
            return ConnectionResult(
                ConnectionResultCode.SUCCESS,
                "已确认该模型不支持音乐创作工具，仍可聊天。",
            )
        return ConnectionResult(
            ConnectionResultCode.SUCCESS, "已验证该模型支持流式音乐工具调用。"
        )

    def _requires_tool_capability_verification(self, provider_id: str, model_id: str) -> bool:
        if provider_id == "codex":
            return False
        definition = self._definition(provider_id)
        self._load_cached_capabilities(provider_id)
        return (
            model_id not in definition.capabilities
            and (provider_id, model_id) not in self._verified_capabilities
        )

    def _capability_overrides(self, provider_id: str) -> dict[str, ModelCapabilities]:
        self._load_cached_capabilities(provider_id)
        return {
            model_id: capabilities
            for (configured_provider_id, model_id), capabilities in self._verified_capabilities.items()
            if configured_provider_id == provider_id
        }

    def _clear_verified_capabilities(self, provider_id: str, *, persist_empty_cache: bool = True) -> None:
        for key in tuple(self._verified_capabilities):
            if key[0] == provider_id:
                del self._verified_capabilities[key]
        self._capability_cache_loaded.add(provider_id)
        if persist_empty_cache:
            self._settings.setValue(f"{self._prefix(provider_id)}/capabilityCache", "{}")
            self._settings.sync()

    def _load_cached_capabilities(self, provider_id: str) -> None:
        if provider_id in self._capability_cache_loaded:
            return
        self._definition(provider_id)
        self._capability_cache_loaded.add(provider_id)
        raw_cache = self._settings.value(f"{self._prefix(provider_id)}/capabilityCache", "")
        if not isinstance(raw_cache, str) or not raw_cache:
            return
        try:
            payload = json.loads(raw_cache)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("version") != _CAPABILITY_CACHE_VERSION:
            return
        if payload.get("endpointFingerprint") != self._capability_endpoint_fingerprint(provider_id):
            return
        models = payload.get("models")
        if not isinstance(models, dict):
            return
        for model_id, raw_capabilities in models.items():
            capabilities = self._capabilities_from_cache(raw_capabilities)
            if isinstance(model_id, str) and model_id.strip() and capabilities is not None:
                self._verified_capabilities[(provider_id, model_id.strip())] = capabilities

    def _write_cached_capabilities(self, provider_id: str) -> None:
        records = {
            model_id: {
                "supportsTools": capabilities.supports_tools,
                "supportsStructuredOutput": capabilities.supports_structured_output,
                "supportsAudio": capabilities.supports_audio,
                "supportsFiles": capabilities.supports_files,
                "supportsImages": capabilities.supports_images,
                "supportsStreaming": capabilities.supports_streaming,
            }
            for (configured_provider_id, model_id), capabilities in self._verified_capabilities.items()
            if configured_provider_id == provider_id
        }
        payload = {
            "version": _CAPABILITY_CACHE_VERSION,
            "endpointFingerprint": self._capability_endpoint_fingerprint(provider_id),
            "models": records,
        }
        self._settings.setValue(
            f"{self._prefix(provider_id)}/capabilityCache",
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )
        self._settings.sync()

    def _capability_endpoint_fingerprint(self, provider_id: str) -> str:
        definition = self._definition(provider_id)
        if definition.requires_api_key:
            config = self._read_credential_config(provider_id)
            base_url = config.base_url if config is not None else definition.default_base_url
        else:
            base_url = str(self._settings.value(f"{self._prefix(provider_id)}/baseUrl", definition.default_base_url))
        return self._endpoint_fingerprint(validate_provider_endpoint(base_url, allow_local_http=True))

    @staticmethod
    def _capabilities_from_cache(raw_capabilities: object) -> ModelCapabilities | None:
        if not isinstance(raw_capabilities, dict):
            return None
        values = (
            raw_capabilities.get("supportsTools"),
            raw_capabilities.get("supportsStructuredOutput"),
            raw_capabilities.get("supportsAudio"),
            raw_capabilities.get("supportsFiles"),
            raw_capabilities.get("supportsStreaming"),
        )
        if any(not isinstance(value, bool) for value in values):
            return None
        images = raw_capabilities.get('supportsImages')
        if images is not None and not isinstance(images, bool):
            return None
        return ModelCapabilities(*values, supports_images=images)

    def _definition(self, provider_id: str) -> ProviderDefinition:
        if self._is_custom_profile(provider_id):
            return _DEFINITIONS_BY_ID["compatible"]
        if not isinstance(provider_id, str) or provider_id not in _DEFINITIONS_BY_ID:
            raise ValueError("unsupported provider")
        return _DEFINITIONS_BY_ID[provider_id]

    def _prefix(self, provider_id: str) -> str:
        if self._is_custom_profile(provider_id):
            return f"{_CUSTOM_PROFILE_ROOT}/{provider_id}"
        return f"{SETTINGS_ROOT}/{provider_id}"

    def _is_custom_profile(self, provider_id: object) -> bool:
        return (
            isinstance(provider_id, str)
            and provider_id.startswith("compatible-")
            and self._settings.contains(f"{_CUSTOM_PROFILE_ROOT}/{provider_id}/profileId")
        )

    def _custom_profile_ids(self) -> tuple[str, ...]:
        self._settings.beginGroup(_CUSTOM_PROFILE_ROOT)
        try:
            candidates = tuple(self._settings.childGroups())
        finally:
            self._settings.endGroup()
        return tuple(profile_id for profile_id in candidates if self._is_custom_profile(profile_id))

    def _require_custom_profile(self, provider_id: str) -> None:
        if self._is_custom_profile(provider_id):
            return
        if isinstance(provider_id, str) and provider_id in _DEFINITIONS_BY_ID:
            raise ValueError("official provider profiles cannot be deleted or renamed")
        raise ValueError("unknown custom provider profile")

    def _write_custom_profile_metadata(
        self, provider_id: str, *, display_name: str, model_id: str, model_source: str
    ) -> None:
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("profile display name is required")
        if not isinstance(model_id, str):
            raise ValueError("model id must be text")
        self._settings.beginGroup(f"{_CUSTOM_PROFILE_ROOT}/{provider_id}")
        try:
            self._settings.setValue("profileId", provider_id)
            self._settings.setValue("providerType", "compatible")
            self._settings.setValue("displayName", display_name.strip())
            self._settings.setValue("baseUrl", "")
            self._settings.setValue("credentialRef", "")
            self._settings.setValue("defaultModel", model_id.strip())
            self._settings.setValue("modelSource", model_source)
            self._settings.setValue("capabilityCache", "{}")
        finally:
            self._settings.endGroup()
        self._settings.sync()

    def _profile_metadata(
        self, provider_id: str, definition: ProviderDefinition, custom: bool
    ) -> dict[str, object]:
        model_source = "official"
        if custom:
            model_source = str(self._settings.value(f"{self._prefix(provider_id)}/modelSource", "manual"))
        return {
            "profileId": provider_id,
            "connectionKind": "subscription" if provider_id == "codex" else
                "local" if provider_id == "ollama" else "custom" if custom or provider_id == "compatible" else definition.connection_kind,
            "brandId": definition.brand_id or definition.provider_id,
            "recommendationPriority": provider_priority(provider_id),
            "providerType": "compatible" if custom else definition.provider_id,
            "isOfficial": not custom,
            "canDelete": custom,
            "modelSource": model_source,
            "capabilityState": self.capability_state(
                provider_id, str(self._settings.value(f"{self._prefix(provider_id)}/modelId", ""))
            ) if self._settings.contains(f"{self._prefix(provider_id)}/modelId") else "unknown",
        }

    def _read_credential_config(self, provider_id: str) -> ProviderConfig | None:
        definition = self._definition(provider_id)
        if not definition.requires_api_key or not self._settings.contains(f"{self._prefix(provider_id)}/providerId"):
            return None
        return ProviderConfig.read_from_settings(self._settings, self._prefix(provider_id))

    def _read_display_name(self, provider_id: str, default: str) -> str:
        self._settings.beginGroup(self._prefix(provider_id))
        try:
            name = str(self._settings.value("displayName", default))
        finally:
            self._settings.endGroup()
        previous_defaults = {
            "openai": {"OpenAI", "OpenAI API"},
            "anthropic": {"Claude / Anthropic", "Anthropic API"},
            "qwen": {"通义千问 · 百炼（北京）", "通义千问 · 百炼"},
            "kimi": {"Kimi / Moonshot（中国）", "Kimi · Moonshot"},
            "compatible": {"OpenAI Compatible"},
        }
        return default if name in previous_defaults.get(provider_id, set()) else name

    @staticmethod
    def _endpoint_fingerprint(endpoint: ProviderEndpoint) -> str:
        return hashlib.sha256(endpoint.url.encode("utf-8")).hexdigest()

    def _is_third_party_endpoint(
        self, provider_id: str, endpoint: ProviderEndpoint
    ) -> bool:
        definition = self._definition(provider_id)
        if not definition.requires_api_key or endpoint.host in _LOOPBACK_HOSTS:
            return False
        if provider_id == "compatible" or self._is_custom_profile(provider_id):
            return True
        default_endpoint = validate_provider_endpoint(definition.default_base_url, allow_local_http=False)
        return endpoint.url != default_endpoint.url


def map_connection_error(error: BaseException, credential: str | None = None) -> ConnectionResult:
    """Map transport failures to stable beginner-facing messages without details."""

    if isinstance(error, CodexError):
        code = {"authentication": ConnectionResultCode.INVALID_KEY, "timeout": ConnectionResultCode.TIMEOUT,
                "provider_unavailable": ConnectionResultCode.NETWORK_ERROR}.get(error.code, ConnectionResultCode.UNKNOWN_ERROR)
        return ConnectionResult(code, str(error))

    scrubber = SecretScrubber()
    raw = scrubber.scrub(error)
    if credential:
        raw = raw.replace(credential, SecretScrubber.REDACTED)
    text = raw.lower()
    if isinstance(error, (socket.timeout, TimeoutError)) or "timed out" in text or "timeout" in text:
        return ConnectionResult(ConnectionResultCode.TIMEOUT, "连接超时，请检查网络或本机服务后重试。")
    if isinstance(error, ValueError) or "provider url" in text or "base url" in text or "redirect" in text:
        return ConnectionResult(ConnectionResultCode.INVALID_BASE_URL, "Base URL 无效，请检查地址与 HTTPS 设置。")
    if isinstance(error, CredentialNotFoundError) or any(marker in text for marker in ("401", "403", "unauthorized", "invalid api key", "authentication")):
        return ConnectionResult(ConnectionResultCode.INVALID_KEY, "API Key 无效、缺失或没有访问权限。")
    if "model not found" in text or "unknown model" in text:
        return ConnectionResult(ConnectionResultCode.MODEL_NOT_FOUND, "找不到该模型，请检查模型 ID。")
    if "unsupported model" in text or "does not support" in text:
        return ConnectionResult(ConnectionResultCode.UNSUPPORTED_MODEL, "该模型不支持当前所需能力，请换一个模型。")
    if isinstance(error, URLError) or "request failed" in text or "network" in text:
        return ConnectionResult(ConnectionResultCode.NETWORK_ERROR, "无法连接到服务，请检查网络、Base URL 或本机服务。")
    return ConnectionResult(ConnectionResultCode.UNKNOWN_ERROR, "连接未完成，请稍后重试。")
