import logging
from typing import Any

from google import genai
from google.genai import types

from src.ai.config import RuntimeClassificationSettings, RuntimeResponseSettings
from src.ai.llm_clients.interfaces import LLMClientInterface

__all__ = (
    "GoogleLLMClient",
)


class GoogleLLMClient(LLMClientInterface):
    def __init__(
        self,
        api_key: str,
    ) -> None:
        self.logger = logging.getLogger("google_llm")
        self._client = genai.Client(api_key=api_key)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        settings: RuntimeClassificationSettings | RuntimeResponseSettings,
        session_id: str,
        system_prompt: str,
        tools: list | None = None,
    ) -> str:
        contents: list[types.Content] = []
        system_instruction = system_prompt or ""
        for message in messages:
            text = message.get("content", "").strip()
            if not text:
                continue

            role = message.get("role", "user")
            if role not in {"user", "model"}:  # Gemini supports 'user' & 'model' roles only
                role = "user"

            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=text)],
                ),
            )

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_output_tokens": settings.max_tokens,
        }

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        try:
            response = await self._client.aio.models.generate_content(
                model=settings.model,
                contents=contents,
                config=config,
            )
            text = response.text or ""
            return text

        except Exception as exc:
            self.logger.warning(
                "llm.google.error",
                extra={
                    "session_id": session_id,
                    "error": str(exc),
                },
            )
            return ""
