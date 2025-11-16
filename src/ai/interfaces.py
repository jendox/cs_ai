from typing import Any, Protocol


class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        user_id: str,
        system_prompt: str,
        session_id: str | None = None,
        tools: list | None = None,
    ): ...
