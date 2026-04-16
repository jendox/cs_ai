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
        system_prompt: str,
        tools: list | None = None,
        json_output: bool = False,
    ) -> str:
        contents: list[types.Content] = []
        system_instruction = system_prompt or ""
        for message in messages:
            text = message.get("content", "").strip()
            if not text:
                continue

            raw_role = message.get("role", "user")
            # Gemini supports 'user' & 'model' roles only
            role = "model" if raw_role == "assistant" else "user"

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

        if tools:
            self.logger.info("chat", extra={"tools": [t.__name__ for t in tools]})
            config_kwargs["tools"] = tools

        if json_output:
            # Gemini API: constrain output to JSON so ticket classification (and
            # similar callers) do not get prose-only replies that break parsing.
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "category": types.Schema(
                        type=types.Type.STRING,
                        format="enum",
                        enum=["customer_support", "marketing_or_spam"],
                    ),
                    "confidence": types.Schema(type=types.Type.NUMBER),
                },
                required=["category", "confidence"],
            )

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
            error = str(exc)
            self.logger.warning(
                "llm.google.error: %s",
                error,
                extra={"error": error, "model": settings.model},
            )
            return ""
