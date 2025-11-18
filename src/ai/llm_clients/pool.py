import logging

from src.ai.llm_clients.google import GoogleLLMClient
from src.ai.llm_clients.interfaces import LLMClientInterface
from src.config import LLMProvider, LLMSettings

__all__ = (
    "LLMClientPool",
)


def _get_llm_client(provider: LLMProvider, api_key: str) -> LLMClientInterface:
    if provider == LLMProvider.GOOGLE:
        return GoogleLLMClient(api_key=api_key)
    raise ValueError(f"Unsupported LLM provider: {provider.value}")


class LLMClientPool:
    def __init__(self, llm_settings: LLMSettings):
        self._settings = llm_settings
        self._clients: dict[LLMProvider, LLMClientInterface] = {}
        self.logger = logging.getLogger("llm_client_pool")

    @property
    def llm_settings(self) -> LLMSettings:
        return self._settings

    def get_client(self, provider: LLMProvider | None) -> LLMClientInterface:
        provider = provider or self._settings.default_provider
        if provider in self._clients:
            return self._clients[provider]

        provider_settings = self._settings.get_provider_settings(provider)
        client = _get_llm_client(provider, provider_settings.api_key.get_secret_value())
        self._clients[provider] = client
        self.logger.info("client.created", extra={"provider": provider.value})

        return client
