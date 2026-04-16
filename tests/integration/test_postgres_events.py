"""Postgres: events deduplication and comment lookup."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.repositories import EventsRepository, TicketsRepository
from src.db.sa import session_local
from src.libs.zendesk_client.models import Brand, Comment, Ticket, TicketStatus
from src.zendesk.models import comment_to_event


@pytest.mark.asyncio
async def test_insert_event_is_idempotent_on_same_key(db_engine: None) -> None:
    ticket = Ticket(
        id=880_001,
        brand=Brand.SUPERSELF,
        status=TicketStatus.OPEN,
        updated_at=datetime.now(tz=UTC),
    )
    comment = Comment(
        id=12_345,
        author_id=999_001,
        body="hello",
        public=True,
        created_at=datetime.now(tz=UTC),
    )
    event = comment_to_event(ticket.id, comment)

    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        events_repo = EventsRepository(session)
        async with session.begin():
            assert await tickets_repo.upsert_ticket_and_check_new(ticket, observing=True) is True
            assert await events_repo.insert_event(event) is True
            assert await events_repo.insert_event(event) is False


@pytest.mark.asyncio
async def test_get_comment_created_at(db_engine: None) -> None:
    ticket = Ticket(
        id=880_002,
        brand=Brand.SUPERSELF,
        status=TicketStatus.OPEN,
        updated_at=datetime.now(tz=UTC),
    )
    created = datetime(2026, 2, 3, 10, 15, tzinfo=UTC)
    comment = Comment(
        id=99_001,
        author_id=999_002,
        body="ping",
        public=True,
        created_at=created,
    )
    event = comment_to_event(ticket.id, comment)

    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        events_repo = EventsRepository(session)
        async with session.begin():
            await tickets_repo.upsert_ticket_and_check_new(ticket, observing=True)
            assert await events_repo.insert_event(event) is True

    async with session_local() as session:
        events_repo = EventsRepository(session)
        found = await events_repo.get_comment_created_at(ticket_id=ticket.id, source_id=str(comment.id))
        assert found == created
