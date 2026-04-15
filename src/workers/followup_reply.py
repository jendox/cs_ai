import uuid
from contextlib import asynccontextmanager
from typing import Any

from pydantic import ValidationError

from src.ai.context import LLMContext
from src.ai.reply_generator import LLMReplyGenerator
from src.db import session_local
from src.db.models import LLMPromptKey
from src.db.repositories import (
    OurPostsRepository,
    TicketNotFound,
    TicketReplyAttemptsRepository,
    TicketsRepository,
    ZendeskRuntimeSettingsRepository,
)
from src.jobs.models import JobType, UserReplyMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import AGENT_IDS, Brand
from src.logs.filters import log_ctx
from src.services import Service
from src.workers.reply_posting import ReplyPostingContext, ReplyPostingService


@asynccontextmanager
async def log_context(ticket_id: int, brand: Brand, iteration_id: str):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.FOLLOWUP_REPLY.value,
        "ticket_id": ticket_id,
        "iteration_id": iteration_id,
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass


class FollowUpReplyWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        llm_context: LLMContext,
        brand: Brand,
    ) -> None:
        super().__init__(name="followup_reply", brand=brand)
        self._zendesk_client = zendesk_client
        self._llm_context = llm_context
        self._reply_generator = LLMReplyGenerator(llm_context)
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.FOLLOWUP_REPLY,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        message = self._parse_message(payload)
        if not message:
            return True

        ticket_id = message.ticket_id
        iteration_id = uuid.uuid4().hex[:8]
        async with (log_context(ticket_id, self.brand, iteration_id)):
            async with session_local() as session:
                tickets_repo = TicketsRepository(session)
                our_posts_repo = OurPostsRepository(session)
                reply_attempts_repo = TicketReplyAttemptsRepository(session)
                zendesk_repo = ZendeskRuntimeSettingsRepository(session)
                reply_posting_service = ReplyPostingService(
                    zendesk_client=self._zendesk_client,
                    logger=self.logger,
                    our_posts_repo=our_posts_repo,
                    reply_attempts_repo=reply_attempts_repo,
                )

                async with session.begin():
                    try:
                        ticket = await tickets_repo.get_ticket_by_id(ticket_id)
                        if not ticket.observing:
                            self.logger.info("ticket.not_observing")
                            return True
                    except TicketNotFound:
                        self.logger.info("ticket.not_found")
                        return True

                    channel = await zendesk_repo.get_channel()
                    reply = await self._generate_followup_reply(ticket_id)
                    return await reply_posting_service.post_reply(
                        context=ReplyPostingContext(
                            ticket_id=ticket_id,
                            brand_id=self.brand.value,
                            job_type=JobType.FOLLOWUP_REPLY.value,
                            channel=channel,
                            prompt_key=LLMPromptKey.FOLLOWUP_REPLY.value,
                            iteration_id=iteration_id,
                        ),
                        reply=reply,
                    )

    def _parse_message(self, payload: dict) -> UserReplyMessage | None:
        try:
            return UserReplyMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None

    async def _generate_followup_reply(self, ticket_id: int) -> str:
        try:
            messages = await self._build_conversation_messages(ticket_id)
            if not messages:
                self.logger.warning("conversation.empty")
                return ""

            system_prompt = await self._llm_context.prompt_storage.followup_reply_prompt(self.brand)
            reply = await self._reply_generator.generate(
                messages=messages,
                system_prompt=system_prompt,
                brand=self.brand,
            )
            if not reply:
                # Пустой ответ — странно; можно вернуть False для retry или True, чтобы не зациклиться.
                self.logger.warning("ai.empty_body")
            return reply
        except Exception as exc:
            self.logger.warning("ai.generate_failed", extra={"error": str(exc)})
            return ""

    async def _build_conversation_messages(self, ticket_id: int) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        comments = await self._zendesk_client.get_ticket_comments(ticket_id)

        for comment in sorted(comments, key=lambda x: x.created_at):
            if not comment.public:
                continue
            body = (comment.body or "").strip()
            if not body:
                continue
            role = "assistant" if comment.author_id in AGENT_IDS else "user"
            messages.append({"role": role, "content": body})

        return messages
