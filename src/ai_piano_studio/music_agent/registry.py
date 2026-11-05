from __future__ import annotations

from ai_piano_studio.music_agent.providers import ModelProvider, ProviderError


class ModelProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider_id: str, provider: ModelProvider) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider id is required")
        if provider_id in self._providers:
            raise ValueError("provider id is already registered")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise ProviderError("configured provider is unavailable") from error
