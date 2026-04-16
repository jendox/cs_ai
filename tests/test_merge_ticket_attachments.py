"""Web admin timeline attachment merge (stored wins over live)."""

from __future__ import annotations

from src.web_admin.routes.tickets import TimelineAttachment, _merge_comment_attachments


def test_merge_comment_attachments_preserves_stored_when_both_present() -> None:
    stored_item = TimelineAttachment(
        file_name="stored.bin",
        content_type=None,
        size_label=None,
        content_url="https://stored",
        thumbnail_url=None,
    )
    live_item = TimelineAttachment(
        file_name="live.bin",
        content_type=None,
        size_label=None,
        content_url="https://live",
        thumbnail_url=None,
    )
    merged = _merge_comment_attachments(
        {"9": (stored_item,)},
        {"9": (live_item,)},
    )
    assert merged["9"][0].content_url == "https://stored"


def test_merge_comment_attachments_fills_missing_from_live() -> None:
    live_item = TimelineAttachment(
        file_name="only-live.bin",
        content_type=None,
        size_label=None,
        content_url="https://live",
        thumbnail_url=None,
    )
    merged = _merge_comment_attachments({}, {"9": (live_item,)})
    assert merged["9"][0].file_name == "only-live.bin"
