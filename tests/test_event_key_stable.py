"""Zendesk event identity: stable keys for deduplication."""

from __future__ import annotations

from datetime import UTC, datetime

from src.zendesk.models import Event, EventKind, EventSourceType, get_md5_hash


def test_event_key_stable_for_same_ticket_source() -> None:
    created = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    a = Event(
        ticket_id=55,
        source_type=EventSourceType.COMMENT,
        source_id="1001",
        kind=EventKind.COMMENT_PUBLIC,
        author_id=10,
        body="x",
        created_at=created,
        inserted_at=created,
    )
    b = Event(
        ticket_id=55,
        source_type=EventSourceType.COMMENT,
        source_id="1001",
        kind=EventKind.COMMENT_PUBLIC,
        author_id=10,
        body="y",
        created_at=created,
        inserted_at=created,
    )
    assert a.event_key == b.event_key


def test_get_md5_hash_matches_event_key_material() -> None:
    ticket_id = 55
    source_type = EventSourceType.COMMENT.value
    source_id = "1001"
    expected = get_md5_hash(f"{ticket_id}:{source_type}:{source_id}")
    created = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    event = Event(
        ticket_id=ticket_id,
        source_type=EventSourceType.COMMENT,
        source_id=source_id,
        kind=EventKind.COMMENT_PUBLIC,
        author_id=10,
        body="x",
        created_at=created,
        inserted_at=created,
    )
    assert event.event_key == expected
