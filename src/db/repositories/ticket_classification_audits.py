from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import TicketClassificationAudit as TicketClassificationAuditEntity
from src.db.repositories.base import BaseRepository

__all__ = (
    "CLASSIFICATION_DECISION_CUSTOMER",
    "CLASSIFICATION_DECISION_SERVICE",
    "CLASSIFICATION_DECISION_UNKNOWN",
    "CLASSIFICATION_SOURCE_LLM",
    "CLASSIFICATION_SOURCE_MANUAL",
    "CLASSIFICATION_SOURCE_RULE",
    "TicketClassificationAuditCreate",
    "TicketClassificationAuditsRepository",
)

CLASSIFICATION_DECISION_CUSTOMER = "customer"
CLASSIFICATION_DECISION_SERVICE = "service"
CLASSIFICATION_DECISION_UNKNOWN = "unknown"
CLASSIFICATION_SOURCE_RULE = "rule"
CLASSIFICATION_SOURCE_LLM = "llm"
CLASSIFICATION_SOURCE_MANUAL = "manual"


@dataclass(frozen=True, kw_only=True)
class TicketClassificationAuditCreate:
    ticket_id: int
    brand_id: int
    decision: str
    source: str
    rule: str | None = None
    detail: str | None = None
    llm_category: str | None = None
    llm_confidence: float | None = None
    threshold: float | None = None


class TicketClassificationAuditsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="ticket_classification_audits_repository", session=session)

    async def create(
        self,
        data: TicketClassificationAuditCreate,
    ) -> TicketClassificationAuditEntity:
        audit = TicketClassificationAuditEntity(
            ticket_id=data.ticket_id,
            brand_id=data.brand_id,
            decision=data.decision,
            source=data.source,
            rule=data.rule,
            detail=data.detail,
            llm_category=data.llm_category,
            llm_confidence=data.llm_confidence,
            threshold=data.threshold,
            created_at=datetime_utils.utcnow(),
        )
        self._session.add(audit)
        await self._session.flush()
        return audit

    async def get_latest_by_ticket(
        self,
        ticket_id: int,
    ) -> TicketClassificationAuditEntity | None:
        stmt = (
            select(TicketClassificationAuditEntity)
            .where(TicketClassificationAuditEntity.ticket_id == ticket_id)
            .order_by(
                desc(TicketClassificationAuditEntity.created_at),
                desc(TicketClassificationAuditEntity.id),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def list_by_ticket(
        self,
        ticket_id: int,
    ) -> list[TicketClassificationAuditEntity]:
        stmt = (
            select(TicketClassificationAuditEntity)
            .where(TicketClassificationAuditEntity.ticket_id == ticket_id)
            .order_by(
                desc(TicketClassificationAuditEntity.created_at),
                desc(TicketClassificationAuditEntity.id),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
