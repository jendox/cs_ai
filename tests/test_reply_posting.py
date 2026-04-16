"""ReplyPostingService: deduplication, empty replies, Zendesk errors."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.db.models import PostChannel
from src.db.repositories.reply_attempts import ReplyAttemptCreate
from src.workers.reply_posting import ReplyPostingContext, ReplyPostingService


def _ctx() -> ReplyPostingContext:
    return ReplyPostingContext(
        ticket_id=10,
        brand_id=99,
        job_type="initial_reply",
        channel=PostChannel.INTERNAL,
        prompt_key="initial_reply",
        iteration_id="abc",
    )


@pytest.mark.asyncio
async def test_post_reply_empty_records_attempt_and_returns_false() -> None:
    zendesk = AsyncMock()
    attempts = AsyncMock()
    attempts.create_empty_reply = AsyncMock(return_value=SimpleNamespace(id=5))
    our_posts = AsyncMock()
    svc = ReplyPostingService(
        zendesk_client=zendesk,
        logger=logging.getLogger("test.reply"),
        our_posts_repo=our_posts,
        reply_attempts_repo=attempts,
    )
    ok = await svc.post_reply(_ctx(), "")
    assert ok is False
    attempts.create_empty_reply.assert_awaited_once()
    call = attempts.create_empty_reply.await_args.args[0]
    assert isinstance(call, ReplyAttemptCreate)
    assert call.ticket_id == 10
    our_posts.record_our_post.assert_not_called()
    zendesk.add_comment.assert_not_called()


@pytest.mark.asyncio
async def test_post_reply_duplicate_marks_skipped_and_returns_true() -> None:
    zendesk = AsyncMock()
    attempts = AsyncMock()
    attempts.create_generated = AsyncMock(return_value=SimpleNamespace(id=7))
    our_posts = AsyncMock()
    our_posts.record_our_post = AsyncMock(return_value=False)
    svc = ReplyPostingService(
        zendesk_client=zendesk,
        logger=logging.getLogger("test.reply"),
        our_posts_repo=our_posts,
        reply_attempts_repo=attempts,
    )
    ok = await svc.post_reply(_ctx(), "hello")
    assert ok is True
    attempts.create_generated.assert_awaited_once()
    attempts.mark_skipped_duplicate.assert_awaited_once_with(7)
    zendesk.add_comment.assert_not_called()


@pytest.mark.asyncio
async def test_post_reply_success_marks_posted() -> None:
    zendesk = AsyncMock()
    attempts = AsyncMock()
    attempts.create_generated = AsyncMock(return_value=SimpleNamespace(id=8))
    our_posts = AsyncMock()
    our_posts.record_our_post = AsyncMock(return_value=True)
    svc = ReplyPostingService(
        zendesk_client=zendesk,
        logger=logging.getLogger("test.reply"),
        our_posts_repo=our_posts,
        reply_attempts_repo=attempts,
    )
    ok = await svc.post_reply(_ctx(), "hello")
    assert ok is True
    zendesk.add_comment.assert_awaited_once()
    attempts.mark_posted.assert_awaited_once_with(8, zendesk_comment_id=None)


@pytest.mark.asyncio
async def test_post_reply_zendesk_failure_deletes_our_post_and_marks_failed() -> None:
    request = httpx.Request("POST", "https://example.test/zendesk")
    zendesk = AsyncMock()
    zendesk.add_comment = AsyncMock(side_effect=httpx.RequestError("boom", request=request))
    attempts = AsyncMock()
    attempts.create_generated = AsyncMock(return_value=SimpleNamespace(id=9))
    our_posts = AsyncMock()
    our_posts.record_our_post = AsyncMock(return_value=True)
    svc = ReplyPostingService(
        zendesk_client=zendesk,
        logger=logging.getLogger("test.reply"),
        our_posts_repo=our_posts,
        reply_attempts_repo=attempts,
    )
    ok = await svc.post_reply(_ctx(), "hello")
    assert ok is False
    our_posts.delete_our_post.assert_awaited_once()
    attempts.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_reply_body_hash_is_md5_hex() -> None:
    svc = ReplyPostingService(
        zendesk_client=AsyncMock(),
        logger=logging.getLogger("test.reply"),
        our_posts_repo=AsyncMock(),
        reply_attempts_repo=AsyncMock(),
    )
    digest = svc._reply_body_hash("alpha")
    assert len(digest) == 32
    assert digest == svc._reply_body_hash("alpha")
    assert digest != svc._reply_body_hash("beta")
