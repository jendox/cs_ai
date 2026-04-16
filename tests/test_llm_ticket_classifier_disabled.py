"""LLM ticket classifier when runtime classification is disabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ai.ticket_classifier import LLMTicketClassifier
from src.libs.zendesk_client.models import Brand, FromTo, Source, Ticket, Via


@pytest.mark.asyncio
async def test_decide_when_classification_disabled_treats_as_customer() -> None:
    runtime = AsyncMock()
    runtime.get_classification = AsyncMock(
        return_value=MagicMock(
            enabled=False,
            threshold=0.8,
        ),
    )
    ctx = MagicMock()
    ctx.runtime_storage = runtime

    classifier = LLMTicketClassifier(ctx)
    ticket = Ticket(
        id=1,
        brand=Brand.SUPERSELF,
        subject="Hi",
        description="Help",
        via=Via(
            channel="email",
            source=Source(
                from_=FromTo(address="u@example.com"),
                rel=None,
                to_=FromTo(),
            ),
        ),
    )
    decision = await classifier.decide(ticket)
    assert decision.is_service is False
    assert decision.error is None
    ctx.client_pool.get_client.assert_not_called()
