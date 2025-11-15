from datetime import timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import Lock as LockEntity
from src.db.repositories.base import BaseRepository

__all__ = (
    "AcquireLockError",
    "LocksRepository",
)


class AcquireLockError(RuntimeError):
    def __init__(self, name: str, holder: str | None):
        super().__init__(f"Lock '{name}' is held by {holder!r}")
        self.name = name
        self.holder = holder


class LocksRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="locks_repository", session=session)

    async def acquire_lock(
        self,
        *,
        name: str,
        holder: str,
        ttl_seconds: int,
    ) -> None:
        now = datetime_utils.utcnow()
        until = now + timedelta(seconds=ttl_seconds)
        stmt = (
            pg_insert(LockEntity)
            .values(name=name, holder=holder, until=until)
            .on_conflict_do_update(
                index_elements=[LockEntity.name],
                set_={"holder": holder, "until": until},
                where=LockEntity.until <= now,
            )
            .returning(LockEntity.name)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            current_holder = await self._session.scalar(
                select(LockEntity.holder).where(LockEntity.name == name),
            )
            raise AcquireLockError(name, current_holder)
        self.logger.debug("lock.acquired", extra={"data": {"name": name, "holder": holder}})

    async def release_lock(self, *, name: str, holder: str):
        stmt = delete(LockEntity).where(
            and_(
                LockEntity.name == name,
                LockEntity.holder == holder,
            ),
        )
        await self._session.execute(stmt)
        self.logger.debug("lock.released", extra={"data": {"name": name, "holder": holder}})
