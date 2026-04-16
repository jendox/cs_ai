from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import TicketCommentAttachment as TicketCommentAttachmentEntity
from src.db.repositories.base import BaseRepository
from src.libs.zendesk_client.models import Attachment, Comment

__all__ = (
    "TicketCommentAttachmentCreate",
    "TicketCommentAttachmentsRepository",
)

MAX_FILE_NAME_LENGTH = 255
MAX_CONTENT_TYPE_LENGTH = 128


@dataclass(frozen=True, kw_only=True)
class TicketCommentAttachmentCreate:
    ticket_id: int
    comment_id: str
    attachment_id: int
    file_name: str
    content_type: str | None
    size: int | None
    content_url: str | None
    mapped_content_url: str | None
    thumbnail_url: str | None
    comment_created_at: datetime | None


class TicketCommentAttachmentsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="ticket_comment_attachments_repository", session=session)

    async def upsert_many(self, attachments: list[TicketCommentAttachmentCreate]) -> int:
        if not attachments:
            return 0

        now = datetime_utils.utcnow()
        rows = [
            {
                **attachment.__dict__,
                "created_at": now,
                "updated_at": now,
            }
            for attachment in attachments
        ]
        stmt = pg_insert(TicketCommentAttachmentEntity).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                TicketCommentAttachmentEntity.ticket_id,
                TicketCommentAttachmentEntity.comment_id,
                TicketCommentAttachmentEntity.attachment_id,
            ],
            set_={
                "file_name": stmt.excluded.file_name,
                "content_type": stmt.excluded.content_type,
                "size": stmt.excluded.size,
                "content_url": stmt.excluded.content_url,
                "mapped_content_url": stmt.excluded.mapped_content_url,
                "thumbnail_url": stmt.excluded.thumbnail_url,
                "comment_created_at": stmt.excluded.comment_created_at,
                "updated_at": now,
            },
        )
        result = await self._session.execute(stmt)
        return result.rowcount or 0

    async def upsert_from_comments(self, ticket_id: int, comments: list[Comment]) -> int:
        return await self.upsert_many(_attachments_from_comments(ticket_id, comments))

    async def list_by_ticket(self, ticket_id: int) -> list[TicketCommentAttachmentEntity]:
        stmt = (
            select(TicketCommentAttachmentEntity)
            .where(TicketCommentAttachmentEntity.ticket_id == ticket_id)
            .order_by(
                TicketCommentAttachmentEntity.comment_created_at,
                TicketCommentAttachmentEntity.id,
            )
        )
        return list(await self._session.scalars(stmt))


def _attachments_from_comments(ticket_id: int, comments: list[Comment]) -> list[TicketCommentAttachmentCreate]:
    result: list[TicketCommentAttachmentCreate] = []
    for comment in comments:
        if comment.id is None:
            continue
        for attachment in comment.attachments:
            item = _attachment_create(ticket_id=ticket_id, comment=comment, attachment=attachment)
            if item is not None:
                result.append(item)
    return result


def _attachment_create(
    *,
    ticket_id: int,
    comment: Comment,
    attachment: Attachment,
) -> TicketCommentAttachmentCreate | None:
    if attachment.id is None:
        return None

    thumbnail = attachment.thumbnails[0] if attachment.thumbnails else None
    thumbnail_url = None
    if thumbnail is not None:
        thumbnail_url = thumbnail.mapped_content_url or thumbnail.content_url

    file_name = attachment.file_name or f"attachment-{attachment.id}"
    return TicketCommentAttachmentCreate(
        ticket_id=ticket_id,
        comment_id=str(comment.id),
        attachment_id=attachment.id,
        file_name=file_name[:MAX_FILE_NAME_LENGTH],
        content_type=attachment.content_type[:MAX_CONTENT_TYPE_LENGTH] if attachment.content_type else None,
        size=attachment.size,
        content_url=attachment.content_url,
        mapped_content_url=attachment.mapped_content_url,
        thumbnail_url=thumbnail_url,
        comment_created_at=comment.created_at,
    )
