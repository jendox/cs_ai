from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Event as EventEntity
from src.db.repositories.base import BaseRepository
from src.zendesk.models import Event

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
