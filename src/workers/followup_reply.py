import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.context import LLMContext
from src.ai.reply_generator import LLMReplyGenerator
from src.db import session_local
from src.db.models import LLMPromptKey
from src.db.repositories import (
    EventsRepository,
    OurPostsRepository,
    TicketNotFound,
    TicketReplyAttemptsRepository,
    TicketsRepository,
    ZendeskRuntimeSettingsRepository,
)
from src.jobs.models import JobType, UserReplyMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import AGENT_IDS, Brand, Comment
from src.services import Service
from src.services.ticket_attachments import store_ticket_comment_attachments
from src.workers.reply_posting import ReplyPostingContext, ReplyPostingService
from src.zendesk.models import comment_to_event

from .log_context import log_context


class FollowUpDecision(StrEnum):
    PROCESS = "process"
    SKIP = "skip"
    RETRY = "retry"


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
        self.brand = cast(Brand, self.brand)

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
        async with (log_context(ticket_id, self.brand, iteration_id, JobType.FOLLOWUP_REPLY)):
            async with session_local() as session:
                tickets_repo = TicketsRepository(session)
                events_repo = EventsRepository(session)
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

                    followup_decision = await self._should_process_followup(
                        events_repo=events_repo,
                        our_posts_repo=our_posts_repo,
                        ticket_id=ticket_id,
                        source_id=message.source_id,
                    )
                    if followup_decision == FollowUpDecision.RETRY:
                        return False
                    if followup_decision == FollowUpDecision.SKIP:
                        return True

                    channel = await zendesk_repo.get_channel()
                    reply = await self._generate_followup_reply(session, ticket_id, events_repo)
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

    async def _should_process_followup(
        self,
        *,
        events_repo: EventsRepository,
        our_posts_repo: OurPostsRepository,
        ticket_id: int,
        source_id: str,
    ) -> FollowUpDecision:
        source_created_at = await events_repo.get_comment_created_at(ticket_id=ticket_id, source_id=source_id)
        if source_created_at is None:
            self.logger.warning("followup.source_event_missing", extra={"source_id": source_id})
            return FollowUpDecision.RETRY

        if await our_posts_repo.exists_before(ticket_id=ticket_id, created_at=source_created_at):
            return FollowUpDecision.PROCESS

        self.logger.info("followup.skipped_before_initial_reply", extra={"source_id": source_id})
        return FollowUpDecision.SKIP

    async def _generate_followup_reply(
        self,
        session: AsyncSession,
        ticket_id: int,
        events_repo: EventsRepository,
    ) -> str:
        try:
            messages = await self._build_conversation_messages(session, ticket_id, events_repo)
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

    async def _build_conversation_messages(
        self,
        session: AsyncSession,
        ticket_id: int,
        events_repo: EventsRepository,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        comments = await self._zendesk_client.get_ticket_comments(ticket_id)
        await self._store_comments(
            session=session,
            events_repo=events_repo,
            ticket_id=ticket_id,
            comments=comments,
        )

        for comment in sorted(comments, key=self._comment_created_at):
            if not comment.public:
                continue
            body = (comment.body or "").strip()
            if not body:
                continue
            role = "assistant" if comment.author_id in AGENT_IDS else "user"
            messages.append({"role": role, "content": body})

        return messages

    async def _store_comments(
        self,
        *,
        session: AsyncSession,
        events_repo: EventsRepository,
        ticket_id: int,
        comments: list[Comment],
    ) -> None:
        inserted_count = 0
        for comment in comments:
            if comment.id is None or comment.created_at is None:
                continue
            if await events_repo.insert_event(comment_to_event(ticket_id, comment)):
                inserted_count += 1
        if inserted_count:
            self.logger.info("comments.stored", extra={"count": inserted_count})
        attachments_count = await store_ticket_comment_attachments(session, ticket_id=ticket_id, comments=comments)
        if attachments_count:
            self.logger.info("comment_attachments.stored", extra={"count": attachments_count})

    @staticmethod
    def _comment_created_at(comment: Comment) -> datetime:
        return comment.created_at or datetime.min.replace(tzinfo=UTC)
