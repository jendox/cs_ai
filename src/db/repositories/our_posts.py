import hashlib

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import OurPost as OurPostEntity
from src.db.repositories.base import BaseRepository

__all__ = (
    "OurPostsRepository",
)


class OurPostsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="our_posts_repository", session=session)

    async def record_our_post(self, *, ticket_id: int, body_hash: str, channel: str = "private") -> bool:
        post_key = hashlib.md5(f"{ticket_id}:{body_hash}".encode()).hexdigest()
        stmt = (
            pg_insert(OurPostEntity)
            .values(
                post_key=post_key,
                ticket_id=ticket_id,
                body_hash=body_hash,
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
