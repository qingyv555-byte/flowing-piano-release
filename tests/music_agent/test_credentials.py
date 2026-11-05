from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from ai_piano_studio.music_agent.config import ProviderConfig
from ai_piano_studio.music_agent.credentials import (
    CredentialNotFoundError,
    CredentialStoreError,
    KeyringCredentialStore,
    MemoryCredentialStore,
)


def test_provider_config_persists_only_credential_reference(tmp_path) -> None:
    settings_path = tmp_path / "assistant.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    credentials = MemoryCredentialStore()
    reference = credentials.put("openai", "fake-phase-one-key")
    config = ProviderConfig(
        provider_id="openai",
        base_url="https://api.openai.com/v1",
        credential_reference=reference,
        model_id="gpt-test",
    )

    config.write_to_settings(settings, "assistant/providers/openai")

    persisted = settings_path.read_text(encoding="utf-8")
    assert "fake-phase-one-key" not in persisted
    restored = ProviderConfig.read_from_settings(
        QSettings(str(settings_path), QSettings.Format.IniFormat),
        "assistant/providers/openai",
    )
    assert restored == config
    assert credentials.get(reference) == "fake-phase-one-key"


def test_memory_credential_store_replaces_and_deletes_only_the_referenced_secret() -> None:
    credentials = MemoryCredentialStore()
    reference = credentials.put("deepseek", "fake-old-key")

    credentials.replace(reference, "fake-new-key")

    assert credentials.get(reference) == "fake-new-key"
    credentials.delete(reference)
    with pytest.raises(CredentialNotFoundError, match="credential is unavailable"):
        credentials.get(reference)


class _FakeKeyringBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


def test_keyring_credential_store_keeps_secret_outside_its_reference() -> None:
    backend = _FakeKeyringBackend()
    credentials = KeyringCredentialStore(backend=backend)

    reference = credentials.put("openai", "fake-phase-one-key")

    assert reference.entry_id != "fake-phase-one-key"
    assert credentials.get(reference) == "fake-phase-one-key"
    assert set(backend.values.values()) == {"fake-phase-one-key"}


def test_runtime_can_load_the_declared_system_keyring_backend() -> None:
    try:
        KeyringCredentialStore()
    except CredentialStoreError as error:
        pytest.fail(f"declared keyring backend was unavailable: {error}")


def test_provider_config_refuses_an_endpoint_that_cannot_make_a_safe_request() -> None:
    credentials = MemoryCredentialStore()
    reference = credentials.put("openai", "fake-phase-one-key")

    with pytest.raises(ValueError):
        ProviderConfig(
            provider_id="openai",
            base_url="file:///C:/secrets.txt",
            credential_reference=reference,
        )
