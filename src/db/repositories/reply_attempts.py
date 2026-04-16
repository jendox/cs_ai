from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, String, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import (
    PostChannel,
    ReplyAttemptStatus,
    TicketReplyAttempt as TicketReplyAttemptEntity,
)
from src.db.repositories.base import BaseRepository
from src.jobs.models import JobType

RECENT_REPLY_ATTEMPT_LIMIT = 50
MAX_REPLY_ATTEMPT_LIMIT = 100


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


@dataclass(frozen=True)
class ReplyAttemptFilters:
    ticket_id: int | None = None
    ticket_id_prefix: str | None = None
    status: ReplyAttemptStatus | None = None
    job_type: JobType | None = None
    brand_id: int | None = None
    created_from: datetime | None = None


@dataclass(frozen=True)
class ReplyAttemptListResult:
    items: list[TicketReplyAttemptEntity]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class ReplyAttemptJobSummary:
    job_type: str
    total: int
    status_counts: dict[ReplyAttemptStatus, int]

    def status_count(self, status: ReplyAttemptStatus) -> int:
        return self.status_counts.get(status, 0)

    @property
    def generated_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.GENERATED)

    @property
    def posted_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.POSTED)

    @property
    def failed_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.FAILED)

    @property
    def duplicate_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.SKIPPED_DUPLICATE)

    @property
    def empty_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.EMPTY_REPLY)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.posted_count / self.total * 100


@dataclass(frozen=True)
class ReplyAttemptSummary:
    total: int
    status_counts: dict[ReplyAttemptStatus, int]
    job_type_counts: dict[str, int]
    job_summaries: list[ReplyAttemptJobSummary]

    def status_count(self, status: ReplyAttemptStatus) -> int:
        return self.status_counts.get(status, 0)

    @property
    def generated_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.GENERATED)

    @property
    def posted_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.POSTED)

    @property
    def failed_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.FAILED)

    @property
    def duplicate_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.SKIPPED_DUPLICATE)

    @property
    def empty_count(self) -> int:
        return self.status_count(ReplyAttemptStatus.EMPTY_REPLY)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.posted_count / self.total * 100


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

    @staticmethod
    def _ticket_filter_condition(filters: ReplyAttemptFilters) -> ColumnElement[bool] | None:
        if filters.ticket_id is not None:
            return TicketReplyAttemptEntity.ticket_id == filters.ticket_id
        if filters.ticket_id_prefix is not None:
            return TicketReplyAttemptEntity.ticket_id.cast(String).like(f"{filters.ticket_id_prefix}%")
        return None

    @staticmethod
    def _attempt_filter_conditions(filters: ReplyAttemptFilters | None) -> list[ColumnElement[bool]]:
        if filters is None:
            return []

        conditions: list[ColumnElement[bool]] = []
        ticket_condition = TicketReplyAttemptsRepository._ticket_filter_condition(filters)
        if ticket_condition is not None:
            conditions.append(ticket_condition)
        if filters.status is not None:
            conditions.append(TicketReplyAttemptEntity.status == filters.status)
        if filters.job_type is not None:
            conditions.append(TicketReplyAttemptEntity.job_type == filters.job_type.value)
        if filters.brand_id is not None:
            conditions.append(TicketReplyAttemptEntity.brand_id == filters.brand_id)
        if filters.created_from is not None:
            conditions.append(TicketReplyAttemptEntity.created_at >= filters.created_from)

        return conditions

    async def list_attempts(
        self,
        *,
        filters: ReplyAttemptFilters | None = None,
        limit: int = RECENT_REPLY_ATTEMPT_LIMIT,
        offset: int = 0,
    ) -> ReplyAttemptListResult:
        limit = min(max(limit, 1), MAX_REPLY_ATTEMPT_LIMIT)
        offset = max(offset, 0)
        conditions = self._attempt_filter_conditions(filters)

        total_stmt = select(func.count()).select_from(TicketReplyAttemptEntity)
        if conditions:
            total_stmt = total_stmt.where(*conditions)

        items_stmt = (
            select(TicketReplyAttemptEntity)
            .order_by(desc(TicketReplyAttemptEntity.created_at))
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            items_stmt = items_stmt.where(*conditions)

        total = await self._session.scalar(total_stmt)
        items = list(await self._session.scalars(items_stmt))

        return ReplyAttemptListResult(
            items=items,
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    async def get_summary(self, *, filters: ReplyAttemptFilters | None = None) -> ReplyAttemptSummary:
        conditions = self._attempt_filter_conditions(filters)

        total_stmt = select(func.count()).select_from(TicketReplyAttemptEntity)
        status_stmt = (
            select(TicketReplyAttemptEntity.status, func.count())
            .group_by(TicketReplyAttemptEntity.status)
        )
        job_type_stmt = (
            select(TicketReplyAttemptEntity.job_type, func.count())
            .group_by(TicketReplyAttemptEntity.job_type)
        )
        job_status_stmt = (
            select(
                TicketReplyAttemptEntity.job_type,
                TicketReplyAttemptEntity.status,
                func.count(),
            )
            .group_by(TicketReplyAttemptEntity.job_type, TicketReplyAttemptEntity.status)
        )
        if conditions:
            total_stmt = total_stmt.where(*conditions)
            status_stmt = status_stmt.where(*conditions)
            job_type_stmt = job_type_stmt.where(*conditions)
            job_status_stmt = job_status_stmt.where(*conditions)

        total = await self._session.scalar(total_stmt)
        status_rows = await self._session.execute(status_stmt)
        job_type_rows = await self._session.execute(job_type_stmt)
        job_status_rows = await self._session.execute(job_status_stmt)
        job_type_counts = dict(job_type_rows.all())
        job_status_counts: dict[str, dict[ReplyAttemptStatus, int]] = {}
        for job_type, status, count in job_status_rows.all():
            job_status_counts.setdefault(job_type, {})[status] = count

        return ReplyAttemptSummary(
            total=total or 0,
            status_counts=dict(status_rows.all()),
            job_type_counts=job_type_counts,
            job_summaries=[
                ReplyAttemptJobSummary(
                    job_type=job_type,
                    total=count,
                    status_counts=job_status_counts.get(job_type, {}),
                )
                for job_type, count in sorted(job_type_counts.items())
            ],
        )
