from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import Checkpoint as CheckpointEntity
from src.db.repositories.base import BaseRepository

__all__ = (
    "CheckpointsRepository",
)


class CheckpointsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="checkpoints_repository", session=session)

    async def get_checkpoint(self, name: str) -> datetime | None:
        result = await self._session.execute(
            select(CheckpointEntity.value)
            .where(CheckpointEntity.name == name),
        )
        return result.scalar_one_or_none()

    async def set_checkpoint(self, name: str, value: datetime):
        now = datetime_utils.utcnow()
        stmt = (
            pg_insert(CheckpointEntity)
            .values(name=name, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=[CheckpointEntity.name],
                set_={"value": value, "updated_at": now},
            )
        )
        await self._session.execute(stmt)
        self.logger.debug("checkpoint.set", extra={"data": {"name": name, "value": str(value)}})
