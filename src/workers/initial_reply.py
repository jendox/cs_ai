import hashlib
import uuid
from contextlib import asynccontextmanager

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.reply_generator import LLMReplyGenerator
from src.ai.ticket_classifier import LLMTicketClassifier
from src.ai.tools.context import LLMContext
from src.db import session_local
from src.db.repositories import OurPostsRepository, TicketsRepository
from src.jobs.models import InitialReplyMessage, JobType
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand, Ticket
from src.logs.filters import log_ctx
from src.services import Service
from src.tickets_filter.cache import tickets_filter_cache


@asynccontextmanager
async def log_context(ticket_id: int, brand: Brand, iteration_id: str):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.INITIAL_REPLY.value,
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


class InitialReplyWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        llm_context: LLMContext,
        brand: Brand,
    ) -> None:
        super().__init__(name="initial_reply", brand=brand)
        self._zendesk_client = zendesk_client
        self._llm_context = llm_context
        self._ticket_classifier = LLMTicketClassifier(
            llm_context.client_pool, llm_context.runtime_storage, llm_context.prompt_storage,
        )
        self._reply_generator = LLMReplyGenerator(llm_context)
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.INITIAL_REPLY,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        message = self._parse_message(payload)
        if not message:
            return True

        ticket = message.ticket
        iteration_id = uuid.uuid4().hex[:8]
        async with log_context(ticket.id, self.brand, iteration_id):
            async with session_local() as session:
                tickets_repo = TicketsRepository(session)
                our_posts_repo = OurPostsRepository(session)

                async with session.begin():
                    # Filter ticket
                    if await self._filter_as_service(session, ticket):
                        return await self._mark_unobserved(tickets_repo, ticket)
                    # Initial reply generation
                    reply = await self._build_ai_reply(ticket)
                    if not reply:
                        return False
                    # Отправка ответа в Zendesk + идемпотентность в БД
                    channel = "private" if self._zendesk_client.review_mode else "public"
                    saved = await self._save_reply(our_posts_repo, reply, ticket.id, channel)
                if not saved:
                    return True
                return await self._post_comment(ticket.id, reply)

    def _parse_message(self, payload: dict) -> InitialReplyMessage | None:
        try:
            return InitialReplyMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None

    async def _filter_as_service(
        self,
        session: AsyncSession,
        ticket: Ticket,
    ) -> bool:
        tickets_filter = await tickets_filter_cache.get_filter(session, self.brand)
        if tickets_filter.is_service_ticket(ticket):
            self.logger.info(
                "tickets.filtered_as_service",
                extra={"decision": "tickets_filter"},
            )
            return True
        # Фильтрация AI
        decision = await self._ticket_classifier.decide(ticket)
        if decision.is_service:
            self.logger.info(
                "tickets.filtered_as_service",
                extra={"decision": "llm_tickets_classifier"},
            )
            return True
        self.logger.info("tickets.filtered_as_customer")
        return False

    async def _mark_unobserved(self, tickets_repo: TicketsRepository, ticket: Ticket) -> bool:
        try:
            await tickets_repo.upsert_ticket_and_check_new(ticket, observing=False)
            self.logger.info("ticket.marked_unobserved", extra={"ticket_id": ticket.id})
            return True
        except Exception as exc:
            self.logger.warning("ticket.update_observing_failed", extra={"ticket_id": ticket.id, "error": str(exc)})
            return False

    async def _build_ai_reply(self, ticket: Ticket) -> str:
        try:
            reply = await self._reply_generator.generate(ticket)
            if not reply:
                self.logger.warning("ai.empty_body", extra={"ticket_id": ticket.id})
            return reply
        except Exception as exc:
            self.logger.warning("ai.generate_failed", extra={"ticket_id": ticket.id, "error": str(exc)})
            return ""

    async def _save_reply(
        self,
        our_posts_repo: OurPostsRepository,
        body: str,
        ticket_id: int,
        channel: str,
    ) -> bool:
        body_hash = hashlib.md5(body.encode()).hexdigest()
        recorded = await our_posts_repo.record_our_post(
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
