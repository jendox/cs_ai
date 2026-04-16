"""Mapping Zendesk comments to internal event records."""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.zendesk_client.models import Comment
from src.zendesk.models import EventKind, EventSourceType, comment_to_event


def test_comment_to_event_public_vs_private() -> None:
    public = Comment(
        id=10,
        author_id=555,
        body="Hi",
        public=True,
        created_at=datetime.now(tz=UTC),
    )
    priv = Comment(
        id=11,
        author_id=556,
        body="Internal",
        public=False,
        created_at=datetime.now(tz=UTC),
    )

    pub_event = comment_to_event(99, public)
    assert pub_event.ticket_id == 99
    assert pub_event.source_type == EventSourceType.COMMENT
    assert pub_event.source_id == "10"
    assert pub_event.kind == EventKind.COMMENT_PUBLIC
    assert pub_event.is_private is False

    priv_event = comment_to_event(99, priv)
    assert priv_event.kind == EventKind.COMMENT_PRIVATE
    assert priv_event.is_private is True
