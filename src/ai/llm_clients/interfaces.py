from typing import Any, Protocol

from src.ai.config import RuntimeClassificationSettings, RuntimeResponseSettings

__all__ = (
    "LLMClientInterface",
)


class LLMClientInterface(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        settings: RuntimeClassificationSettings | RuntimeResponseSettings,
        session_id: str,
        system_prompt: str,
        tools: list | None = None,
    ) -> str: ...
