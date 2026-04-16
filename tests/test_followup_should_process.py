"""Follow-up worker gating: event present, ordering vs initial reply."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brands import Brand
from src.workers.followup_reply import FollowUpDecision, FollowUpReplyWorker


@pytest.mark.asyncio
async def test_followup_retry_when_source_event_missing() -> None:
    events = AsyncMock()
    events.get_comment_created_at = AsyncMock(return_value=None)
    our_posts = AsyncMock()
    worker = FollowUpReplyWorker(
        zendesk_client=AsyncMock(),
        amqp_url="amqp://",
        llm_context=MagicMock(),
        brand=Brand.SUPERSELF,
        brand_id=12345,
    )
    decision = await worker._should_process_followup(
        events_repo=events,
        our_posts_repo=our_posts,
        ticket_id=1,
        source_id="99",
    )
    assert decision == FollowUpDecision.RETRY
    our_posts.exists_before.assert_not_called()


@pytest.mark.asyncio
async def test_followup_skip_when_no_bot_post_before_comment() -> None:
    created = datetime(2026, 1, 2, tzinfo=UTC)
    events = AsyncMock()
    events.get_comment_created_at = AsyncMock(return_value=created)
    our_posts = AsyncMock()
    our_posts.exists_before = AsyncMock(return_value=False)
    worker = FollowUpReplyWorker(
        zendesk_client=AsyncMock(),
        amqp_url="amqp://",
        llm_context=MagicMock(),
        brand=Brand.SUPERSELF,
        brand_id=12345,
    )
    decision = await worker._should_process_followup(
        events_repo=events,
        our_posts_repo=our_posts,
        ticket_id=1,
        source_id="99",
    )
    assert decision == FollowUpDecision.SKIP


@pytest.mark.asyncio
async def test_followup_process_when_bot_post_exists_before_comment() -> None:
    created = datetime(2026, 1, 2, tzinfo=UTC)
    events = AsyncMock()
    events.get_comment_created_at = AsyncMock(return_value=created)
    our_posts = AsyncMock()
    our_posts.exists_before = AsyncMock(return_value=True)
    worker = FollowUpReplyWorker(
        zendesk_client=AsyncMock(),
        amqp_url="amqp://",
        llm_context=MagicMock(),
        brand=Brand.SUPERSELF,
        brand_id=12345,
    )
    decision = await worker._should_process_followup(
        events_repo=events,
        our_posts_repo=our_posts,
        ticket_id=1,
        source_id="99",
    )
    assert decision == FollowUpDecision.PROCESS
