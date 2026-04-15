from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Event as EventEntity
from src.db.repositories.base import BaseRepository
from src.zendesk.models import Event, EventSourceType

__all__ = (
    "EventsRepository",
)


class EventsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="events_repository", session=session)

    async def insert_event(self, event: Event) -> bool:
        stmt = (
            pg_insert(EventEntity)
            .values(**event.model_dump())
            .on_conflict_do_nothing(index_elements=[EventEntity.event_key.key])
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def list_by_ticket(self, ticket_id: int) -> list[EventEntity]:
        stmt = (
            select(EventEntity)
            .where(EventEntity.ticket_id == ticket_id)
            .order_by(EventEntity.created_at)
        )
        return list(await self._session.scalars(stmt))

    async def list_comments_by_ticket(self, ticket_id: int) -> list[EventEntity]:
        stmt = (
            select(EventEntity)
            .where(
                EventEntity.ticket_id == ticket_id,
                EventEntity.source_type == EventSourceType.COMMENT.value,
            )
            .order_by(EventEntity.created_at)
        )
        return list(await self._session.scalars(stmt))
