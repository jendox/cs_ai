"""Attachment extraction from Zendesk comment payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from src.db.repositories.ticket_comment_attachments import _attachments_from_comments
from src.libs.zendesk_client.models import Attachment, AttachmentThumbnail, Comment


def test_attachments_from_comments_skips_comment_without_id() -> None:
    comment = Comment(
        id=None,
        attachments=[Attachment(id=1, file_name="a.png")],
        created_at=datetime.now(tz=UTC),
    )
    assert _attachments_from_comments(5, [comment]) == []


def test_attachments_from_comments_maps_fields() -> None:
    thumb = AttachmentThumbnail(
        id=2,
        content_url="https://cdn/z.png",
        mapped_content_url="https://mapped/z.png",
    )
    comment = Comment(
        id=100,
        attachments=[
            Attachment(
                id=1,
                file_name="photo.png",
                content_type="image/png",
                size=2048,
                content_url="https://cdn/x",
                mapped_content_url="https://mapped/x",
                thumbnails=[thumb],
            ),
        ],
        created_at=datetime.now(tz=UTC),
    )
    rows = _attachments_from_comments(5, [comment])
    assert len(rows) == 1
    row = rows[0]
    assert row.ticket_id == 5
    assert row.comment_id == "100"
    assert row.attachment_id == 1
    assert row.file_name == "photo.png"
    assert row.content_type == "image/png"
    assert row.size == 2048
    assert row.mapped_content_url == "https://mapped/x"
    assert row.thumbnail_url == "https://mapped/z.png"
