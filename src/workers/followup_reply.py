import hashlib
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import ValidationError

from src.ai.context import LLMContext
from src.ai.reply_generator import LLMReplyGenerator
from src.db import session_local
from src.db.repositories import OurPostsRepository, TicketNotFound, TicketsRepository
from src.jobs.models import JobType, UserReplyMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import AGENT_IDS, Brand
from src.logs.filters import log_ctx
from src.services import Service


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
        super().__init__(name="user_reply", brand=brand)
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
        async with log_context(ticket_id, self.brand, iteration_id):
            async with session_local() as session:
                tickets_repo = TicketsRepository(session)
                our_posts_repo = OurPostsRepository(session)

                async with session.begin():
                    try:
                        ticket = await tickets_repo.get_ticket_by_id(ticket_id)
                        if not ticket.observing:
                            self.logger.info("ticket.not_observing")
                            return True
                    except TicketNotFound:
                        self.logger.info("ticket.not_found")
                        return True

                    reply = await self._generate_followup_reply(ticket_id)
                    if not reply:
                        return False
                    channel = "private" if self._zendesk_client.review_mode else "public"

                saved = await self._save_reply(our_posts_repo, reply, ticket_id, channel)
                if not saved:
                    return True
                return await self._post_comment(ticket_id, reply)

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
                self.logger.warning("ai.empty_body", extra={"ticket_id": ticket_id})
            return reply
        except Exception as exc:
            self.logger.warning("ai.generate_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
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

    async def _save_reply(
        self,
        our_post_repo: OurPostsRepository,
        body: str,
        ticket_id: int,
        channel: str,
    ) -> bool:
        body_hash = hashlib.md5(body.encode()).hexdigest()
        recorded = await our_post_repo.record_our_post(
            ticket_id=ticket_id, body_hash=body_hash, body=body, channel=channel,
        )
        if not recorded:
            # такой ответ уже фиксировали — не дублируем
            self.logger.info("our_post.duplicate_skip", extra={"ticket_id": ticket_id})
            return False
        self.logger.info("our_post.reply.saved", extra={"ticket_id": ticket_id})
        return True

    async def _post_comment(self, ticket_id: int, comment: str) -> bool:
        try:
            await self._zendesk_client.add_comment(ticket_id, comment)
            self.logger.info("comment.posted", extra={"ticket_id": ticket_id})
            return True
        except httpx.HTTPError as error:
            self.logger.warning("http_error", extra={"ticket_id": ticket_id, "error": str(error)})
            return False
        except Exception as exc:
            self.logger.error("post_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
            return False
