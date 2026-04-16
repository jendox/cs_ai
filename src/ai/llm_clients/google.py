import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

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

    @staticmethod
    def _body_text_for_schema(
        response: types.GenerateContentResponse,
        response_model: type[BaseModel],
    ) -> str:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_model):
            return parsed.model_dump_json()
        text = response.text or ""
        if text or not response.candidates:
            return text
        chunks: list[str] = []
        for part in response.candidates[0].content.parts or []:
            if part.text and part.thought is not True:
                chunks.append(part.text)
        return "".join(chunks)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        settings: RuntimeClassificationSettings | RuntimeResponseSettings,
        system_prompt: str,
        tools: list | None = None,
        response_model: type[BaseModel] | None = None,
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

        if response_model is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_model
            # Disable thinking for structured-output calls: the model
            # doesn't need to reason, and on thinking-capable models the
            # thinking tokens eat into max_output_tokens, truncating JSON.
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=0,
            )

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        try:
            response = await self._client.aio.models.generate_content(
                model=settings.model,
                contents=contents,
                config=config,
            )
            if response_model is not None:
                return self._body_text_for_schema(response, response_model)
            return response.text or ""

        except Exception as exc:
            error = str(exc)
            self.logger.warning(
                "llm.google.error: %s",
                error,
                extra={"error": error, "model": settings.model},
            )
            return ""
