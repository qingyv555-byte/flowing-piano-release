from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.credentials import CredentialReference
from ai_piano_studio.music_agent.security import (
    ProviderEndpoint,
    validate_provider_endpoint,
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_id: str
    base_url: str
    credential_reference: CredentialReference
    model_id: str = ""
    allow_local_http: bool = False

    def __post_init__(self) -> None:
        provider_id = _required_text(self.provider_id, "provider id")
        _required_text(self.base_url, "provider base URL")
        if self.credential_reference.provider_id != provider_id:
            raise ValueError("credential reference provider does not match configuration")
        if not isinstance(self.model_id, str):
            raise ValueError("model id must be a string")
        if not isinstance(self.allow_local_http, bool):
            raise ValueError("allow_local_http must be a boolean")
        endpoint = validate_provider_endpoint(self.base_url, self.allow_local_http)
        object.__setattr__(self, "base_url", endpoint.url)

    @property
    def endpoint(self) -> ProviderEndpoint:
        return validate_provider_endpoint(self.base_url, self.allow_local_http)

    def write_to_settings(self, settings: QSettings, prefix: str) -> None:
        _required_text(prefix, "settings prefix")
        settings.beginGroup(prefix)
        try:
            settings.setValue("providerId", self.provider_id)
            settings.setValue("baseUrl", self.base_url)
            settings.setValue("credentialProviderId", self.credential_reference.provider_id)
            settings.setValue("credentialEntryId", self.credential_reference.entry_id)
            settings.setValue("modelId", self.model_id)
            settings.setValue("allowLocalHttp", self.allow_local_http)
        finally:
            settings.endGroup()
        settings.sync()

    @classmethod
    def read_from_settings(cls, settings: QSettings, prefix: str) -> ProviderConfig:
        _required_text(prefix, "settings prefix")
        settings.beginGroup(prefix)
        try:
            provider_id = settings.value("providerId", "")
            base_url = settings.value("baseUrl", "")
            credential_provider_id = settings.value("credentialProviderId", "")
            credential_entry_id = settings.value("credentialEntryId", "")
            model_id = settings.value("modelId", "")
            allow_local_http = settings.value("allowLocalHttp", False, type=bool)
        finally:
            settings.endGroup()
        return cls(
            provider_id=_required_text(provider_id, "provider id"),
            base_url=_required_text(base_url, "provider base URL"),
            credential_reference=CredentialReference(
                _required_text(credential_provider_id, "credential provider id"),
                _required_text(credential_entry_id, "credential entry id"),
            ),
            model_id=_required_text(model_id, "model id") if model_id else "",
            allow_local_http=allow_local_http,
        )
