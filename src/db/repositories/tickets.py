from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, String, case, desc, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import (
    Event as EventEntity,
    ReplyAttemptStatus,
    Ticket as TicketEntity,
    TicketClassificationAudit as TicketClassificationAuditEntity,
    TicketReplyAttempt as TicketReplyAttemptEntity,
)
from src.db.repositories.base import BaseRepository
from src.db.repositories.ticket_classification_audits import (
    CLASSIFICATION_DECISION_CUSTOMER,
    CLASSIFICATION_DECISION_SERVICE,
    CLASSIFICATION_DECISION_UNKNOWN,
)
from src.libs.zendesk_client.models import Brand, Ticket, TicketStatus

__all__ = (
    "TicketFilters",
    "TicketListItem",
    "TicketListResult",
    "TicketNotFound",
    "TicketsRepository",
)

MAX_ACTIVE_TICKETS_LIMIT = 20
RECENT_TICKETS_LIMIT = 50
MAX_TICKETS_LIMIT = 100


@dataclass(frozen=True)
class TicketFilters:
    ticket_id_prefix: str | None = None
    status: TicketStatus | None = None
    brand: Brand | None = None
    observing: bool | None = None
    classification_decision: str | None = None
    classification_source: str | None = None


@dataclass(frozen=True)
class TicketListItem:
    ticket: TicketEntity
    event_count: int
    reply_attempt_count: int
    posted_reply_count: int
    failed_reply_count: int
    last_customer_comment_at: datetime | None
    classification_decision: str | None
    classification_source: str | None
    classification_rule: str | None
    classification_detail: str | None
    classification_created_at: datetime | None
    llm_category: str | None
    llm_confidence: float | None


@dataclass(frozen=True)
class TicketListResult:
    items: list[TicketListItem]
    total: int
    limit: int
    offset: int


class TicketNotFound(Exception): ...


class TicketsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="tickets_repository", session=session)

    async def upsert_ticket_and_check_new(
        self,
        ticket: Ticket,
        observing: bool = True,
        last_seen_at: datetime | None = None,
    ) -> bool:
        """
        Upsert a ticket into the database.

        Performs an INSERT or UPDATE operation for the given ticket. If a ticket with the same ID
        already exists, it will be updated. Otherwise, a new ticket will be inserted.

        observing := CASE WHEN tickets.observing = FALSE THEN FALSE ELSE EXCLUDED.observing END
        !!! Important: This method never change observing from False to True !!!

        Args:
            ticket: The Ticket object to upsert
            observing: Whether the ticket is being observed (default: True)
            last_seen_at: Timestamp when the ticket was last seen. If None, uses current UTC time

        Returns:
            bool: True if the ticket was newly inserted, False if an existing ticket was updated
        """
        last_seen_at = last_seen_at or datetime_utils.utcnow()
        existed = await self._session.scalar(
            select(TicketEntity.ticket_id).where(TicketEntity.ticket_id == ticket.id),
        )

        stmt = pg_insert(TicketEntity).values(
            ticket_id=ticket.id,
            brand_id=ticket.brand.value,
            status=ticket.status.value,
            updated_at=ticket.updated_at,
            observing=observing,
            last_seen_at=last_seen_at,
        )
        # - observing: сохраняем False, если он уже False; иначе берём входящее значение (EXCLUDED.observing)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TicketEntity.ticket_id],
            set_={
                "brand_id": ticket.brand.value,
                "status": ticket.status.value,
                "updated_at": ticket.updated_at,
                "last_seen_at": last_seen_at,
                "observing": case(
                    (TicketEntity.observing == literal(False), literal(False)),
                    else_=stmt.excluded.observing,
                ),
            },
        )
        await self._session.execute(stmt)
        return existed is None

    async def get_ticket_by_id(self, ticket_id: int) -> TicketEntity:
        stmt = (
            select(TicketEntity)
            .where(TicketEntity.ticket_id == ticket_id)
        )
        ticket = await self._session.scalar(stmt)
        if ticket is None:
            raise TicketNotFound(f"Ticket {ticket_id} doesn't exist.")
        return ticket

    async def get_ticket_status(self, ticket_id: int) -> str:
        ticket = await self.get_ticket_by_id(ticket_id)
        return ticket.status

    @staticmethod
    def _ticket_filter_conditions(filters: TicketFilters | None) -> list[ColumnElement[bool]]:
        if filters is None:
            return []

        conditions: list[ColumnElement[bool]] = []
        if filters.ticket_id_prefix is not None:
            conditions.append(TicketEntity.ticket_id.cast(String).like(f"{filters.ticket_id_prefix}%"))
        if filters.status is not None:
            conditions.append(TicketEntity.status == filters.status.value)
        if filters.brand is not None:
            conditions.append(TicketEntity.brand_id == filters.brand.value)
        if filters.observing is not None:
            conditions.append(TicketEntity.observing.is_(filters.observing))

        return conditions

    @staticmethod
    def _classification_filter_conditions(
        filters: TicketFilters | None,
        *,
        audit: type[TicketClassificationAuditEntity],
    ) -> list[ColumnElement[bool]]:
        if filters is None:
            return []

        conditions: list[ColumnElement[bool]] = []
        if filters.classification_decision == "unknown":
            conditions.append((audit.id.is_(None)) | (audit.decision == CLASSIFICATION_DECISION_UNKNOWN))
        elif filters.classification_decision in {
            CLASSIFICATION_DECISION_CUSTOMER,
            CLASSIFICATION_DECISION_SERVICE,
            CLASSIFICATION_DECISION_UNKNOWN,
        }:
            conditions.append(audit.decision == filters.classification_decision)

        if filters.classification_source in {"rule", "llm"}:
            conditions.append(audit.source == filters.classification_source)

        return conditions

    async def list_tickets(
        self,
        *,
        filters: TicketFilters | None = None,
        limit: int = RECENT_TICKETS_LIMIT,
        offset: int = 0,
    ) -> TicketListResult:
        limit = min(max(limit, 1), MAX_TICKETS_LIMIT)
        offset = max(offset, 0)
        conditions = self._ticket_filter_conditions(filters)
        event_counts = (
            select(
                EventEntity.ticket_id,
                func.count().label("event_count"),
                func.max(
                    case(
                        (EventEntity.author_role == "user", EventEntity.created_at),
                    ),
                ).label("last_customer_comment_at"),
            )
            .group_by(EventEntity.ticket_id)
            .subquery()
        )
        reply_counts = (
            select(
                TicketReplyAttemptEntity.ticket_id,
                func.count().label("reply_attempt_count"),
                func.sum(
                    case(
                        (TicketReplyAttemptEntity.status == ReplyAttemptStatus.POSTED, 1),
                        else_=0,
                    ),
                ).label("posted_reply_count"),
                func.sum(
                    case(
                        (TicketReplyAttemptEntity.status == ReplyAttemptStatus.FAILED, 1),
                        else_=0,
                    ),
                ).label("failed_reply_count"),
            )
            .group_by(TicketReplyAttemptEntity.ticket_id)
            .subquery()
        )
        latest_audit_ids = (
            select(
                TicketClassificationAuditEntity.ticket_id,
                func.max(TicketClassificationAuditEntity.id).label("audit_id"),
            )
            .group_by(TicketClassificationAuditEntity.ticket_id)
            .subquery()
        )
        audit_conditions = self._classification_filter_conditions(
            filters,
            audit=TicketClassificationAuditEntity,
        )

        total_stmt = (
            select(func.count())
            .select_from(TicketEntity)
            .outerjoin(latest_audit_ids, latest_audit_ids.c.ticket_id == TicketEntity.ticket_id)
            .outerjoin(
                TicketClassificationAuditEntity,
                TicketClassificationAuditEntity.id == latest_audit_ids.c.audit_id,
            )
        )
        items_stmt = (
            select(
                TicketEntity,
                func.coalesce(event_counts.c.event_count, 0),
                func.coalesce(reply_counts.c.reply_attempt_count, 0),
                func.coalesce(reply_counts.c.posted_reply_count, 0),
                func.coalesce(reply_counts.c.failed_reply_count, 0),
                event_counts.c.last_customer_comment_at,
                TicketClassificationAuditEntity.decision,
                TicketClassificationAuditEntity.source,
                TicketClassificationAuditEntity.rule,
                TicketClassificationAuditEntity.detail,
                TicketClassificationAuditEntity.created_at,
                TicketClassificationAuditEntity.llm_category,
                TicketClassificationAuditEntity.llm_confidence,
            )
            .outerjoin(event_counts, event_counts.c.ticket_id == TicketEntity.ticket_id)
            .outerjoin(reply_counts, reply_counts.c.ticket_id == TicketEntity.ticket_id)
            .outerjoin(latest_audit_ids, latest_audit_ids.c.ticket_id == TicketEntity.ticket_id)
            .outerjoin(
                TicketClassificationAuditEntity,
                TicketClassificationAuditEntity.id == latest_audit_ids.c.audit_id,
            )
            .order_by(desc(TicketEntity.updated_at))
            .limit(limit)
            .offset(offset)
        )
        if conditions:
            total_stmt = total_stmt.where(*conditions)
            items_stmt = items_stmt.where(*conditions)
        if audit_conditions:
            total_stmt = total_stmt.where(*audit_conditions)
            items_stmt = items_stmt.where(*audit_conditions)

        total = await self._session.scalar(total_stmt)
        rows = await self._session.execute(items_stmt)
        items = [
            TicketListItem(
                ticket=ticket,
                event_count=event_count,
                reply_attempt_count=reply_attempt_count,
                posted_reply_count=posted_reply_count,
                failed_reply_count=failed_reply_count,
                last_customer_comment_at=last_customer_comment_at,
                classification_decision=classification_decision,
                classification_source=classification_source,
                classification_rule=classification_rule,
                classification_detail=classification_detail,
                classification_created_at=classification_created_at,
                llm_category=llm_category,
                llm_confidence=llm_confidence,
            )
            for (
                ticket,
                event_count,
                reply_attempt_count,
                posted_reply_count,
                failed_reply_count,
                last_customer_comment_at,
                classification_decision,
                classification_source,
                classification_rule,
                classification_detail,
                classification_created_at,
                llm_category,
                llm_confidence,
            ) in rows.all()
        ]

        return TicketListResult(
            items=items,
            total=total or 0,
            limit=limit,
            offset=offset,
        )

    async def set_observing(
        self,
        ticket_id: int,
        *,
        observing: bool,
        last_seen_at: datetime | None = None,
    ) -> None:
        now = last_seen_at or datetime_utils.utcnow()
        stmt = (
            update(TicketEntity)
            .where(TicketEntity.ticket_id == ticket_id)
            .values(
                observing=observing,
                last_seen_at=now,
            )
        )
        await self._session.execute(stmt)

    async def get_observing_tickets(self, limit: int | None = None) -> list[TicketEntity]:
        if limit is None:
            limit = MAX_ACTIVE_TICKETS_LIMIT
        stmt = (
            select(TicketEntity)
            .where(TicketEntity.observing.is_(True))
            .order_by(TicketEntity.updated_at.desc())
            .limit(min(limit, MAX_ACTIVE_TICKETS_LIMIT))
        )
        result = await self._session.scalars(stmt)
        return list(result)
