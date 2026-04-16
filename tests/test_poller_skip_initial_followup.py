"""Poller helper behavior (no Zendesk / DB I/O)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.zendesk.models import Event, EventAuthorRole, EventKind, EventSourceType
from src.zendesk.poller import Poller


def _comment_event(*, ticket_id: int, author_role: EventAuthorRole) -> Event:
    author_id = 999999999999 if author_role == EventAuthorRole.USER else 372174069320
    return Event(
        ticket_id=ticket_id,
        source_type=EventSourceType.COMMENT,
        source_id="42",
        kind=EventKind.COMMENT_PUBLIC,
        author_id=author_id,
        body="hello",
        created_at=datetime.now(tz=UTC),
        inserted_at=datetime.now(tz=UTC),
    )


def test_should_skip_initial_followup_for_initial_ticket_user_comment() -> None:
    event = _comment_event(ticket_id=7, author_role=EventAuthorRole.USER)
    assert Poller._should_skip_initial_followup(event, {7}) is True


def test_should_not_skip_when_ticket_not_in_initial_set() -> None:
    event = _comment_event(ticket_id=7, author_role=EventAuthorRole.USER)
    assert Poller._should_skip_initial_followup(event, set()) is False


def test_should_not_skip_for_agent_comment_even_if_initial_ticket() -> None:
    event = _comment_event(ticket_id=7, author_role=EventAuthorRole.AGENT)
    assert Poller._should_skip_initial_followup(event, {7}) is False
