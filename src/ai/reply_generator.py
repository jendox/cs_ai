import logging
from contextlib import asynccontextmanager
from typing import Any

from src.ai import utils
from src.ai.config import RuntimeResponseSettings
from src.ai.config.prompt import LLMPrompt
from src.ai.context import LLMCallContext, LLMContext, llm_call_ctx
from src.ai.llm_clients import LLMClientInterface
from src.ai.tools import amazon_tools
from src.libs.zendesk_client.models import Brand


@asynccontextmanager
async def llm_call_context(brand: Brand):
    token = llm_call_ctx.set(LLMCallContext(brand=brand))
    try:
        yield
    finally:
        llm_call_ctx.reset(token)


class LLMReplyGenerator:
    def __init__(self, llm_context: LLMContext) -> None:
        self._llm_context = llm_context
        self.logger = logging.getLogger("llm_reply_generator")

    async def _response_settings(self) -> RuntimeResponseSettings:
        return await self._llm_context.runtime_storage.get_response()

    async def _make_llm_request(
        self,
        client: LLMClientInterface,
        settings: RuntimeResponseSettings,
        messages: list[dict[str, Any]],
        system_prompt: LLMPrompt,
    ) -> str:
        try:
            text = await client.chat(
                messages=messages,
                settings=settings,
                system_prompt=system_prompt.text,
                tools=amazon_tools,
            )
            self.logger.info("make_llm_request.success", extra={"text": text[:200]})
            return text
        except Exception as exc:
            self.logger.warning("make_llm_request.failed", extra={"error": str(exc)})
            return ""

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: LLMPrompt,
        brand: Brand,
    ) -> str:
        async with llm_call_context(brand):
            settings = await self._response_settings()
            client, cfg = utils.resolve_llm_client_and_cfg(self._llm_context, settings)

            return await self._make_llm_request(client, cfg, messages, system_prompt)
