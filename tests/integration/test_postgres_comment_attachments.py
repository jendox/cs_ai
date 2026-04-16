"""Postgres: ticket_comment_attachments upsert."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.repositories import TicketCommentAttachmentsRepository, TicketsRepository
from src.db.sa import session_local
from src.libs.zendesk_client.models import Attachment, Brand, Comment, Ticket, TicketStatus


@pytest.mark.asyncio
async def test_comment_attachments_upsert_is_idempotent(db_engine: None) -> None:
    ticket = Ticket(
        id=880_020,
        brand=Brand.SUPERSELF,
        status=TicketStatus.OPEN,
        updated_at=datetime.now(tz=UTC),
    )
    comment = Comment(
        id=50_001,
        author_id=111,
        body="see file",
        public=True,
        created_at=datetime.now(tz=UTC),
        attachments=[
            Attachment(
                id=77_001,
                file_name="doc.pdf",
                content_type="application/pdf",
                size=128,
                content_url="https://example.invalid/a",
                mapped_content_url="https://example.invalid/mapped",
            ),
        ],
    )

    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        attachments_repo = TicketCommentAttachmentsRepository(session)
        async with session.begin():
            await tickets_repo.upsert_ticket_and_check_new(ticket, observing=True)
            count_first = await attachments_repo.upsert_from_comments(ticket.id, [comment])
            count_second = await attachments_repo.upsert_from_comments(ticket.id, [comment])
            assert count_first >= 1
            assert count_second >= 1

    async with session_local() as session:
        attachments_repo = TicketCommentAttachmentsRepository(session)
        rows = await attachments_repo.list_by_ticket(ticket.id)
        assert len(rows) == 1
        assert rows[0].attachment_id == 77_001
        assert rows[0].file_name == "doc.pdf"
