from typing import Any, Protocol

from pydantic import BaseModel

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
        system_prompt: str,
        tools: list | None = None,
        response_model: type[BaseModel] | None = None,
    ) -> str: ...
