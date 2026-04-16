import uuid
from textwrap import dedent
from typing import cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.context import LLMContext
from src.ai.reply_generator import LLMReplyGenerator
from src.ai.ticket_classifier import LLMTicketClassifier
from src.db import session_local
from src.db.models import LLMPromptKey
from src.db.repositories import (
    CLASSIFICATION_DECISION_CUSTOMER,
    CLASSIFICATION_DECISION_SERVICE,
    CLASSIFICATION_DECISION_UNKNOWN,
    CLASSIFICATION_SOURCE_LLM,
    CLASSIFICATION_SOURCE_RULE,
    OurPostsRepository,
    TicketClassificationAuditCreate,
    TicketClassificationAuditsRepository,
    TicketReplyAttemptsRepository,
    TicketsRepository,
    ZendeskRuntimeSettingsRepository,
)
from src.jobs.models import InitialReplyMessage, JobType
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand, Ticket
from src.services import Service
from src.tickets_filter.cache import tickets_filter_cache

from .log_context import log_context
from .reply_posting import ReplyPostingContext, ReplyPostingService


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
        self._ticket_classifier = LLMTicketClassifier(llm_context)
        self._reply_generator = LLMReplyGenerator(llm_context)
        self._amqp_url = amqp_url
        self.brand = cast(Brand, self.brand)

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
        async with (log_context(ticket.id, self.brand, iteration_id, JobType.INITIAL_REPLY)):
            async with session_local() as session:
                tickets_repo = TicketsRepository(session)
                classification_audits_repo = TicketClassificationAuditsRepository(session)
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
                    if await self._filter_as_service(session, classification_audits_repo, ticket):
                        return await self._mark_unobserved(tickets_repo, ticket)

                    channel = await zendesk_repo.get_channel()
                    reply = await self._generate_initial_reply(ticket)
                    return await reply_posting_service.post_reply(
                        context=ReplyPostingContext(
                            ticket_id=ticket.id,
                            brand_id=self.brand.value,
                            job_type=JobType.INITIAL_REPLY.value,
                            channel=channel,
                            prompt_key=LLMPromptKey.INITIAL_REPLY.value,
                            iteration_id=iteration_id,
                        ),
                        reply=reply,
                    )

    def _parse_message(self, payload: dict) -> InitialReplyMessage | None:
        try:
            return InitialReplyMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None

    async def _filter_as_service(
        self,
        session: AsyncSession,
        classification_audits_repo: TicketClassificationAuditsRepository,
        ticket: Ticket,
    ) -> bool:
        tickets_filter = await tickets_filter_cache.get_filter(session, self.brand)
        rule_decision = tickets_filter.classify_ticket(ticket)
        if rule_decision.is_service:
            await classification_audits_repo.create(
                TicketClassificationAuditCreate(
                    ticket_id=ticket.id,
                    brand_id=self.brand.value,
                    decision=CLASSIFICATION_DECISION_SERVICE,
                    source=CLASSIFICATION_SOURCE_RULE,
                    rule=rule_decision.rule,
                    detail=rule_decision.detail,
                ),
            )
            self.logger.info(
                "tickets.filtered_as_service",
                extra={
                    "decision": "tickets_filter",
                    "classification_rule": rule_decision.rule,
                    "classification_detail": rule_decision.detail,
                },
            )
            return True
        if rule_decision.rule is not None:
            await classification_audits_repo.create(
                TicketClassificationAuditCreate(
                    ticket_id=ticket.id,
                    brand_id=self.brand.value,
                    decision=CLASSIFICATION_DECISION_CUSTOMER,
                    source=CLASSIFICATION_SOURCE_RULE,
                    rule=rule_decision.rule,
                    detail=rule_decision.detail,
                ),
            )
            self.logger.info(
                "tickets.filtered_as_customer",
                extra={
                    "decision": "tickets_filter",
                    "classification_rule": rule_decision.rule,
                    "classification_detail": rule_decision.detail,
                },
            )
            return False
        # Фильтрация AI
        decision = await self._ticket_classifier.decide(ticket)
        if decision.error is not None:
            await classification_audits_repo.create(
                TicketClassificationAuditCreate(
                    ticket_id=ticket.id,
                    brand_id=self.brand.value,
                    decision=CLASSIFICATION_DECISION_UNKNOWN,
                    source=CLASSIFICATION_SOURCE_LLM,
                    detail=decision.error,
                    llm_category=decision.category.value,
                    llm_confidence=decision.confidence,
                    threshold=decision.threshold,
                ),
            )
            self.logger.warning(
                "tickets.classification_failed",
                extra={
                    "decision": "llm_tickets_classifier",
                    "error": decision.error,
                    "llm_category": decision.category.value,
                    "llm_confidence": decision.confidence,
                    "threshold": decision.threshold,
                },
            )
            return False

        await classification_audits_repo.create(
            TicketClassificationAuditCreate(
                ticket_id=ticket.id,
                brand_id=self.brand.value,
                decision=CLASSIFICATION_DECISION_SERVICE if decision.is_service else CLASSIFICATION_DECISION_CUSTOMER,
                source=CLASSIFICATION_SOURCE_LLM,
                llm_category=decision.category.value,
                llm_confidence=decision.confidence,
                threshold=decision.threshold,
            ),
        )
        if decision.is_service:
            self.logger.info(
                "tickets.filtered_as_service",
                extra={
                    "decision": "llm_tickets_classifier",
                    "llm_category": decision.category.value,
                    "llm_confidence": decision.confidence,
                    "threshold": decision.threshold,
                },
            )
            return True
        self.logger.info(
            "tickets.filtered_as_customer",
            extra={
                "decision": "llm_tickets_classifier",
                "llm_category": decision.category.value,
                "llm_confidence": decision.confidence,
                "threshold": decision.threshold,
            },
        )
        return False

    async def _mark_unobserved(self, tickets_repo: TicketsRepository, ticket: Ticket) -> bool:
        try:
            await tickets_repo.upsert_ticket_and_check_new(ticket, observing=False)
            self.logger.info("ticket.marked_unobserved", extra={"ticket_id": ticket.id})
            return True
        except Exception as exc:
            self.logger.warning("ticket.update_observing_failed", extra={"ticket_id": ticket.id, "error": str(exc)})
            return False

    async def _generate_initial_reply(self, ticket: Ticket) -> str:
        try:
            content = self._build_initial_reply_message(ticket)
            system_prompt = await self._llm_context.prompt_storage.initial_reply_prompt(self.brand)
            reply = await self._reply_generator.generate(
                messages=[{"role": "user", "content": content}],
                system_prompt=system_prompt,
                brand=self.brand,
            )
            if not reply:
                self.logger.warning("ai.empty_body")
            return reply
        except Exception as exc:
            self.logger.warning("ai.generate_failed", extra={"error": str(exc)})
            return ""

    @staticmethod
    def _build_initial_reply_message(ticket: Ticket) -> str:
        subject = (ticket.subject or ticket.raw_subject or "").strip()
        body = (ticket.description or "").strip()
        via_channel = ticket.via.channel if ticket.via and ticket.via.channel else "unknown"

        return dedent(f"""
            Customer message (via {via_channel}):

            Subject:
            {subject}

            Message:
            {body}
        """).strip()
