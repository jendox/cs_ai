"""Postgres: our_posts deduplication."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.models import PostChannel
from src.db.repositories import OurPostsRepository, TicketsRepository
from src.db.sa import session_local
from src.libs.zendesk_client.models import Ticket, TicketStatus


@pytest.mark.asyncio
async def test_record_our_post_duplicate_returns_false(db_engine: None) -> None:
    ticket = Ticket(
        id=880_010,
        brand_id=12345,
        status=TicketStatus.OPEN,
        updated_at=datetime.now(tz=UTC),
    )
    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        our_posts_repo = OurPostsRepository(session)
        async with session.begin():
            await tickets_repo.upsert_ticket_and_check_new(ticket, observing=True)
            assert await our_posts_repo.record_our_post(
                ticket_id=ticket.id,
                body_hash="a" * 32,
                body="same body",
                channel=PostChannel.INTERNAL,
            ) is True
            assert await our_posts_repo.record_our_post(
                ticket_id=ticket.id,
                body_hash="a" * 32,
                body="same body",
                channel=PostChannel.INTERNAL,
            ) is False
