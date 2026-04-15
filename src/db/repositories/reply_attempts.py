from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import (
    PostChannel,
    ReplyAttemptStatus,
    TicketReplyAttempt as TicketReplyAttemptEntity,
)
from src.db.repositories.base import BaseRepository

RECENT_REPLY_ATTEMPT_LIMIT = 50


@dataclass(frozen=True)
class ReplyAttemptCreate:
    ticket_id: int
    brand_id: int
    job_type: str
    channel: PostChannel
    body_hash: str | None = None
    body: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_key: str | None = None
    iteration_id: str | None = None


class TicketReplyAttemptNotFound(Exception): ...


class TicketReplyAttemptsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="ticket_reply_attempts_repository", session=session)

    async def create_generated(self, data: ReplyAttemptCreate) -> TicketReplyAttemptEntity:
        return await self._create(data, status=ReplyAttemptStatus.GENERATED)

    async def create_empty_reply(self, data: ReplyAttemptCreate) -> TicketReplyAttemptEntity:
        return await self._create(data, status=ReplyAttemptStatus.EMPTY_REPLY)

    async def _create(
        self,
        data: ReplyAttemptCreate,
        *,
        status: ReplyAttemptStatus,
    ) -> TicketReplyAttemptEntity:
        now = datetime_utils.utcnow()
        entity = TicketReplyAttemptEntity(
            ticket_id=data.ticket_id,
            brand_id=data.brand_id,
            job_type=data.job_type,
            channel=data.channel,
            status=status,
            body_hash=data.body_hash,
            body=data.body,
            provider=data.provider,
            model=data.model,
            prompt_key=data.prompt_key,
            iteration_id=data.iteration_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def mark_posted(
        self,
        attempt_id: int,
        *,
        zendesk_comment_id: int | None = None,
    ) -> None:
        entity = await self._get(attempt_id)
        now = datetime_utils.utcnow()
        entity.status = ReplyAttemptStatus.POSTED
        entity.zendesk_comment_id = zendesk_comment_id
        entity.posted_at = now
        entity.updated_at = now
        await self._session.flush()

    async def mark_failed(self, attempt_id: int, *, error: str) -> None:
        entity = await self._get(attempt_id)
        entity.status = ReplyAttemptStatus.FAILED
        entity.error = error
        entity.updated_at = datetime_utils.utcnow()
        await self._session.flush()

    async def mark_skipped_duplicate(self, attempt_id: int) -> None:
        entity = await self._get(attempt_id)
        entity.status = ReplyAttemptStatus.SKIPPED_DUPLICATE
        entity.updated_at = datetime_utils.utcnow()
        await self._session.flush()

    async def list_recent(
        self,
        *,
        limit: int = RECENT_REPLY_ATTEMPT_LIMIT,
    ) -> list[TicketReplyAttemptEntity]:
        stmt = (
            select(TicketReplyAttemptEntity)
            .order_by(desc(TicketReplyAttemptEntity.created_at))
            .limit(limit)
        )
        return list(await self._session.scalars(stmt))

    async def list_by_ticket(self, ticket_id: int) -> list[TicketReplyAttemptEntity]:
        stmt = (
            select(TicketReplyAttemptEntity)
            .where(TicketReplyAttemptEntity.ticket_id == ticket_id)
            .order_by(TicketReplyAttemptEntity.created_at)
        )
        return list(await self._session.scalars(stmt))

    async def _get(self, attempt_id: int) -> TicketReplyAttemptEntity:
        entity = await self._session.get(TicketReplyAttemptEntity, attempt_id)
        if entity is None:
            raise TicketReplyAttemptNotFound(f"Ticket reply attempt {attempt_id} not found.")
        return entity
