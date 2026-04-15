import hashlib
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import OurPost as OurPostEntity, PostChannel
from src.db.repositories.base import BaseRepository

__all__ = (
    "OurPostsRepository",
)


class OurPostsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="our_posts_repository", session=session)

    async def record_our_post(
        self, *,
        ticket_id: int,
        body_hash: str,
        body: str,
        channel: PostChannel,
    ) -> bool:
        post_key = hashlib.md5(f"{ticket_id}:{body_hash}".encode()).hexdigest()
        stmt = (
            pg_insert(OurPostEntity)
            .values(
                post_key=post_key,
                ticket_id=ticket_id,
                body_hash=body_hash,
                body=body,
                channel=channel,
                created_at=datetime_utils.utcnow(),
            )
            .on_conflict_do_nothing(index_elements=[OurPostEntity.post_key])
        )
        try:
            result = await self._session.execute(stmt)
            return result.rowcount == 1
        except IntegrityError as e:
            self.logger.warning("our_post.insert.integrity", extra={"error": str(e.orig)})
            return False
        except DBAPIError as e:
            self.logger.error("our_post.insert.dbapi", extra={"error": str(e.orig)}, exc_info=True)
            raise

    async def delete_our_post(self, *, ticket_id: int, body_hash: str) -> None:
        post_key = hashlib.md5(f"{ticket_id}:{body_hash}".encode()).hexdigest()
        stmt = delete(OurPostEntity).where(OurPostEntity.post_key == post_key)
        await self._session.execute(stmt)

    async def exists_before(self, *, ticket_id: int, created_at: datetime) -> bool:
        stmt = (
            select(OurPostEntity.post_key)
            .where(
                OurPostEntity.ticket_id == ticket_id,
                OurPostEntity.created_at < created_at,
            )
            .limit(1)
        )
        return await self._session.scalar(stmt) is not None
