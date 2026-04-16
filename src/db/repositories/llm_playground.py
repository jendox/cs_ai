from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, String, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import (
    LLMPlaygroundMessage,
    LLMPlaygroundMessageRole,
    LLMPlaygroundRun,
    LLMPlaygroundRunStatus,
    LLMPlaygroundTicket,
    LLMPlaygroundTicketStatus,
)
from src.db.repositories.base import BaseRepository
from src.libs.zendesk_client.models import Brand

RECENT_PLAYGROUND_TICKETS_LIMIT = 50
MAX_PLAYGROUND_TICKETS_LIMIT = 100


class LLMPlaygroundTicketNotFound(Exception): ...


@dataclass(frozen=True)
class LLMPlaygroundTicketCreate:
    brand: Brand
    subject: str
    body: str
    created_by: str


@dataclass(frozen=True)
class LLMPlaygroundRunCreate:
    ticket_id: int
    prompt_key: str
    provider: str | None
    model: str | None
    status: LLMPlaygroundRunStatus
    input_messages: list[dict]
    output_body: str | None
    error: str | None
    created_by: str


@dataclass(frozen=True)
class LLMPlaygroundMessageCreate:
    ticket_id: int
    role: LLMPlaygroundMessageRole
    body: str
    provider: str | None = None
    model: str | None = None
    prompt_key: str | None = None
    run_id: int | None = None


@dataclass(frozen=True)
class LLMPlaygroundFilters:
    ticket_id_prefix: str | None = None
    status: LLMPlaygroundTicketStatus | None = None
    brand: Brand | None = None


@dataclass(frozen=True)
class LLMPlaygroundTicketListItem:
    ticket: LLMPlaygroundTicket
    message_count: int
    run_count: int
    last_message_at: datetime | None


@dataclass(frozen=True)
class LLMPlaygroundTicketListResult:
    items: list[LLMPlaygroundTicketListItem]
    total: int
    limit: int
    offset: int


class LLMPlaygroundRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="llm_playground_repository", session=session)

    async def create_ticket(self, data: LLMPlaygroundTicketCreate) -> LLMPlaygroundTicket:
        now = datetime_utils.utcnow()
        ticket = LLMPlaygroundTicket(
            brand_id=data.brand.value,
            subject=data.subject,
            status=LLMPlaygroundTicketStatus.OPEN,
            created_by=data.created_by,
            created_at=now,
            updated_at=now,
            closed_at=None,
        )
        self._session.add(ticket)
        await self._session.flush()
        await self.add_message(
            LLMPlaygroundMessageCreate(
                ticket_id=ticket.id,
                role=LLMPlaygroundMessageRole.USER,
                body=data.body,
            ),
        )
        return ticket

    async def get_ticket(self, ticket_id: int) -> LLMPlaygroundTicket:
        ticket = await self._session.get(LLMPlaygroundTicket, ticket_id)
        if ticket is None:
            raise LLMPlaygroundTicketNotFound(f"LLM playground ticket {ticket_id} not found.")
        return ticket

    async def add_message(
        self,
        data: LLMPlaygroundMessageCreate,
    ) -> LLMPlaygroundMessage:
        now = datetime_utils.utcnow()
        message = LLMPlaygroundMessage(
            ticket_id=data.ticket_id,
            role=data.role,
            body=data.body,
            provider=data.provider,
            model=data.model,
            prompt_key=data.prompt_key,
            run_id=data.run_id,
            created_at=now,
        )
        self._session.add(message)
        await self._touch_ticket(data.ticket_id, now=now)
        await self._session.flush()
        return message

    async def list_messages(self, ticket_id: int) -> list[LLMPlaygroundMessage]:
        stmt = (
            select(LLMPlaygroundMessage)
            .where(LLMPlaygroundMessage.ticket_id == ticket_id)
            .order_by(LLMPlaygroundMessage.created_at, LLMPlaygroundMessage.id)
        )
        return list(await self._session.scalars(stmt))

    async def create_run(self, data: LLMPlaygroundRunCreate) -> LLMPlaygroundRun:
        now = datetime_utils.utcnow()
        run = LLMPlaygroundRun(
            ticket_id=data.ticket_id,
            prompt_key=data.prompt_key,
            provider=data.provider,
            model=data.model,
            status=data.status,
            input_messages=data.input_messages,
            output_body=data.output_body,
            error=data.error,
            created_by=data.created_by,
            created_at=now,
        )
        self._session.add(run)
        await self._touch_ticket(data.ticket_id, now=now)
        await self._session.flush()
        return run

    async def list_runs(self, ticket_id: int) -> list[LLMPlaygroundRun]:
        stmt = (
            select(LLMPlaygroundRun)
            .where(LLMPlaygroundRun.ticket_id == ticket_id)
            .order_by(desc(LLMPlaygroundRun.created_at), desc(LLMPlaygroundRun.id))
        )
        return list(await self._session.scalars(stmt))

    async def close_ticket(self, ticket_id: int) -> None:
        now = datetime_utils.utcnow()
        ticket = await self.get_ticket(ticket_id)
        ticket.status = LLMPlaygroundTicketStatus.CLOSED
        ticket.closed_at = now
        ticket.updated_at = now
        await self._session.flush()

    @staticmethod
    def _filter_conditions(filters: LLMPlaygroundFilters | None) -> list[ColumnElement[bool]]:
        if filters is None:
            return []

        conditions: list[ColumnElement[bool]] = []
        if filters.ticket_id_prefix is not None:
            conditions.append(LLMPlaygroundTicket.id.cast(String).like(f"{filters.ticket_id_prefix}%"))
        if filters.status is not None:
            conditions.append(LLMPlaygroundTicket.status == filters.status)
        if filters.brand is not None:
            conditions.append(LLMPlaygroundTicket.brand_id == filters.brand.value)
        return conditions

    async def list_tickets(
        self,
        *,
        filters: LLMPlaygroundFilters | None = None,
        limit: int = RECENT_PLAYGROUND_TICKETS_LIMIT,
        offset: int = 0,
    ) -> LLMPlaygroundTicketListResult:
        limit = min(max(limit, 1), MAX_PLAYGROUND_TICKETS_LIMIT)
        offset = max(offset, 0)
        conditions = self._filter_conditions(filters)
        message_counts = (
            select(
                LLMPlaygroundMessage.ticket_id,
                func.count().label("message_count"),
                func.max(LLMPlaygroundMessage.created_at).label("last_message_at"),
            )
            .group_by(LLMPlaygroundMessage.ticket_id)
            .subquery()
        )
        run_counts = (
            select(
                LLMPlaygroundRun.ticket_id,
                func.count().label("run_count"),
            )
            .group_by(LLMPlaygroundRun.ticket_id)
            .subquery()
        )

        total_stmt = select(func.count()).select_from(LLMPlaygroundTicket)
        items_stmt = (
            select(
                LLMPlaygroundTicket,
                func.coalesce(message_counts.c.message_count, 0),
                func.coalesce(run_counts.c.run_count, 0),
                message_counts.c.last_message_at,
            )
            .outerjoin(message_counts, message_counts.c.ticket_id == LLMPlaygroundTicket.id)
            .outerjoin(run_counts, run_counts.c.ticket_id == LLMPlaygroundTicket.id)
            .order_by(desc(LLMPlaygroundTicket.updated_at), desc(LLMPlaygroundTicket.id))
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            total_stmt = total_stmt.where(*conditions)
            items_stmt = items_stmt.where(*conditions)

        total = await self._session.scalar(total_stmt)
        rows = await self._session.execute(items_stmt)
        items = [
            LLMPlaygroundTicketListItem(
                ticket=ticket,
                message_count=message_count,
                run_count=run_count,
                last_message_at=last_message_at,
            )
            for ticket, message_count, run_count, last_message_at in rows.all()
        ]
        return LLMPlaygroundTicketListResult(
            items=items,
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    async def _touch_ticket(self, ticket_id: int, *, now: datetime) -> None:
        ticket = await self.get_ticket(ticket_id)
        ticket.updated_at = now
