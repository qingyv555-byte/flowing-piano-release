from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


class CredentialNotFoundError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("credential is unavailable")


class CredentialStoreError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("credential storage is unavailable")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class CredentialReference:
    provider_id: str
    entry_id: str

    def __post_init__(self) -> None:
        _required_text(self.provider_id, "credential provider id")
        _required_text(self.entry_id, "credential entry id")


class CredentialStore(Protocol):
    def put(self, provider_id: str, secret: str) -> CredentialReference: ...

    def get(self, reference: CredentialReference) -> str: ...

    def replace(self, reference: CredentialReference, secret: str) -> None: ...

    def delete(self, reference: CredentialReference) -> None: ...


class KeyringBackend(Protocol):
    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class KeyringCredentialStore:
    """Credential-store adapter backed by the Windows keyring provider."""

    SERVICE_NAME = "AI Piano Studio Music Agent"

    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        service_name: str = SERVICE_NAME,
    ) -> None:
        self._backend = backend if backend is not None else self._load_backend()
        self._service_name = _required_text(service_name, "credential service name")

    @staticmethod
    def _load_backend() -> KeyringBackend:
        try:
            import keyring
        except ImportError as error:
            raise CredentialStoreError() from error
        return keyring

    def put(self, provider_id: str, secret: str) -> CredentialReference:
        reference = CredentialReference(
            provider_id=_required_text(provider_id, "credential provider id"),
            entry_id=uuid4().hex,
        )
        self._write(reference, secret)
        return reference

    def get(self, reference: CredentialReference) -> str:
        try:
            secret = self._backend.get_password(self._service_name, reference.entry_id)
        except Exception as error:
            raise CredentialStoreError() from error
        if secret is None:
            raise CredentialNotFoundError()
        return secret

    def replace(self, reference: CredentialReference, secret: str) -> None:
        self._write(reference, secret)

    def delete(self, reference: CredentialReference) -> None:
        try:
            self._backend.delete_password(self._service_name, reference.entry_id)
        except Exception as error:
            raise CredentialStoreError() from error

    def _write(self, reference: CredentialReference, secret: str) -> None:
        try:
            self._backend.set_password(
                self._service_name,
                reference.entry_id,
                _required_text(secret, "credential"),
            )
        except Exception as error:
            raise CredentialStoreError() from error


class MemoryCredentialStore:
    """Process-local credential store used only by tests and dependency injection."""

    def __init__(self) -> None:
        self._secrets: dict[CredentialReference, str] = {}

    def put(self, provider_id: str, secret: str) -> CredentialReference:
        reference = CredentialReference(
            provider_id=_required_text(provider_id, "credential provider id"),
            entry_id=uuid4().hex,
        )
        self._secrets[reference] = _required_text(secret, "credential")
        return reference

    def get(self, reference: CredentialReference) -> str:
        try:
            return self._secrets[reference]
        except KeyError as error:
            raise CredentialNotFoundError() from error

    def replace(self, reference: CredentialReference, secret: str) -> None:
        if reference not in self._secrets:
            raise CredentialNotFoundError()
        self._secrets[reference] = _required_text(secret, "credential")

    def delete(self, reference: CredentialReference) -> None:
        if reference not in self._secrets:
            raise CredentialNotFoundError()
        del self._secrets[reference]
