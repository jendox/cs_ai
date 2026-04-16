"""TicketClassificationService orchestration (rules vs LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brands import Brand
from src.db.repositories import CLASSIFICATION_DECISION_CUSTOMER, CLASSIFICATION_DECISION_SERVICE
from src.libs.zendesk_client.models import FromTo, Source, Ticket, Via
from src.services.ticket_classification import TicketClassificationService
from src.tickets_filter.filter import ServiceDecision


def _ticket() -> Ticket:
    return Ticket(
        id=2002,
        brand_id=12345,
        subject="Subject",
        description="Body",
        via=Via(
            channel="email",
            source=Source(
                from_=FromTo(address="user@example.com"),
                rel=None,
                to_=FromTo(),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_classify_rule_service_short_circuits_llm() -> None:
    flt = MagicMock()
    flt.classify_ticket.return_value = ServiceDecision(
        is_service=True,
        rule="sender_strict",
        detail="matched",
    )
    session = AsyncMock()

    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)
    with patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(return_value=flt),
    ):
        result = await svc.classify(session, ticket=_ticket(), brand=Brand.SUPERSELF)

    assert result.is_service is True
    assert result.decision == CLASSIFICATION_DECISION_SERVICE
    llm_ctx.client_pool.get_client.assert_not_called()


@pytest.mark.asyncio
async def test_classify_rule_customer_short_circuits_llm() -> None:
    flt = MagicMock()
    flt.classify_ticket.return_value = ServiceDecision(
        is_service=False,
        rule="api_exception",
        detail="allowed",
    )
    session = AsyncMock()
    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)
    with patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(return_value=flt),
    ):
        result = await svc.classify(session, ticket=_ticket(), brand=Brand.SUPERSELF)

    assert result.is_service is False
    assert result.decision == CLASSIFICATION_DECISION_CUSTOMER
    llm_ctx.client_pool.get_client.assert_not_called()


@pytest.mark.asyncio
async def test_classify_falls_back_to_llm_when_no_rule_decision() -> None:
    flt = MagicMock()
    flt.classify_ticket.return_value = ServiceDecision(is_service=False, rule=None)
    session = AsyncMock()

    llm_decision = MagicMock()
    llm_decision.error = None
    llm_decision.is_service = True
    llm_decision.category = MagicMock(value="marketing_or_spam")
    llm_decision.confidence = 0.95
    llm_decision.threshold = 0.8

    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)
    svc._ticket_classifier.decide = AsyncMock(return_value=llm_decision)

    with patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(return_value=flt),
    ):
        result = await svc.classify(session, ticket=_ticket(), brand=Brand.SUPERSELF)

    assert result.is_service is True
    svc._ticket_classifier.decide.assert_awaited_once()
