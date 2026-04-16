from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai import utils
from src.ai.context import LLMContext
from src.ai.reply_generator import LLMReplyGenerator
from src.brands import Brand
from src.config import get_app_settings
from src.db.models import (
    LLMPlaygroundMessage,
    LLMPlaygroundMessageRole,
    LLMPlaygroundRunStatus,
    LLMPlaygroundTicket,
    LLMPromptKey,
)
from src.db.repositories import LLMPlaygroundMessageCreate, LLMPlaygroundRepository, LLMPlaygroundRunCreate


@dataclass(frozen=True)
class LLMPlaygroundGenerationResult:
    reply: str
    error: str | None


class LLMPlaygroundService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        llm_context: LLMContext,
    ) -> None:
        self._session = session
        self._llm_context = llm_context
        self._reply_generator = LLMReplyGenerator(llm_context)

    async def generate_initial_reply(
        self,
        *,
        ticket: LLMPlaygroundTicket,
        messages: list[LLMPlaygroundMessage],
        created_by: str,
    ) -> LLMPlaygroundGenerationResult:
        brand = get_app_settings().brand.require_brand_for_id(ticket.brand_id)
        first_user_message = self._first_user_message(messages)
        input_messages = [
            {
                "role": "user",
                "content": self._build_initial_reply_message(
                    subject=ticket.subject,
                    body=first_user_message,
                ),
            },
        ]
        system_prompt = await self._llm_context.prompt_storage.initial_reply_prompt(brand, ticket.brand_id)
        return await self._generate_and_store(
            ticket=ticket,
            prompt_key=LLMPromptKey.INITIAL_REPLY.value,
            input_messages=input_messages,
            system_prompt=system_prompt,
            brand=brand,
            created_by=created_by,
        )

    async def generate_followup_reply(
        self,
        *,
        ticket: LLMPlaygroundTicket,
        messages: list[LLMPlaygroundMessage],
        created_by: str,
    ) -> LLMPlaygroundGenerationResult:
        brand = get_app_settings().brand.require_brand_for_id(ticket.brand_id)
        input_messages = self._build_followup_messages(messages)
        if not input_messages:
            return LLMPlaygroundGenerationResult(reply="", error="Conversation is empty.")

        system_prompt = await self._llm_context.prompt_storage.followup_reply_prompt(brand, ticket.brand_id)
        return await self._generate_and_store(
            ticket=ticket,
            prompt_key=LLMPromptKey.FOLLOWUP_REPLY.value,
            input_messages=input_messages,
            system_prompt=system_prompt,
            brand=brand,
            created_by=created_by,
        )

    async def _generate_and_store(
        self,
        *,
        ticket: LLMPlaygroundTicket,
        prompt_key: str,
        input_messages: list[dict[str, Any]],
        system_prompt,
        brand: Brand,
        created_by: str,
    ) -> LLMPlaygroundGenerationResult:
        repo = LLMPlaygroundRepository(self._session)
        response_settings = await self._llm_context.runtime_storage.get_response()
        _, final_settings = utils.resolve_llm_client_and_cfg(self._llm_context, response_settings)
        provider = final_settings.provider.value if final_settings.provider else None
        model = final_settings.model
        try:
            reply = await self._reply_generator.generate(
                messages=input_messages,
                system_prompt=system_prompt,
                brand=brand,
            )
            error = None if reply else "LLM returned an empty response."
        except Exception as exc:
            reply = ""
            error = str(exc)

        run = await repo.create_run(
            LLMPlaygroundRunCreate(
                ticket_id=ticket.id,
                prompt_key=prompt_key,
                provider=provider,
                model=model,
                status=LLMPlaygroundRunStatus.GENERATED if reply else LLMPlaygroundRunStatus.FAILED,
                input_messages=input_messages,
                output_body=reply or None,
                error=error,
                created_by=created_by,
            ),
        )
        if reply:
            await repo.add_message(
                LLMPlaygroundMessageCreate(
                    ticket_id=ticket.id,
                    role=LLMPlaygroundMessageRole.ASSISTANT,
                    body=reply,
                    provider=provider,
                    model=model,
                    prompt_key=prompt_key,
                    run_id=run.id,
                ),
            )

        return LLMPlaygroundGenerationResult(reply=reply, error=error)

    @staticmethod
    def _first_user_message(messages: list[LLMPlaygroundMessage]) -> str:
        for message in messages:
            if message.role == LLMPlaygroundMessageRole.USER:
                return message.body.strip()
        return ""

    @staticmethod
    def _build_initial_reply_message(*, subject: str, body: str) -> str:
        return dedent(f"""
            Customer message (via playground):

            Subject:
            {subject.strip()}

            Message:
            {body.strip()}
        """).strip()

    @staticmethod
    def _build_followup_messages(messages: list[LLMPlaygroundMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            body = message.body.strip()
            if not body or message.role == LLMPlaygroundMessageRole.SYSTEM:
                continue
            result.append({"role": message.role.value, "content": body})
        return result
