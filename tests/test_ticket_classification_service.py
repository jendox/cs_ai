"""TicketClassificationService orchestration (rules vs LLM)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brands import Brand
from src.db.repositories import (
    CLASSIFICATION_DECISION_CUSTOMER,
    CLASSIFICATION_DECISION_SERVICE,
    CLASSIFICATION_SOURCE_MANUAL,
)
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


@pytest.mark.asyncio
async def test_classify_and_store_respects_manual_override() -> None:
    """If the latest audit row is source='manual', subsequent automatic
    reclassifications must respect it and not overwrite it (bug #2)."""
    latest_manual = SimpleNamespace(
        decision=CLASSIFICATION_DECISION_CUSTOMER,
        source=CLASSIFICATION_SOURCE_MANUAL,
        rule=None,
        detail="updated by admin",
        llm_category=None,
        llm_confidence=None,
        threshold=None,
    )
    repo_instance = MagicMock()
    repo_instance.get_latest_by_ticket = AsyncMock(return_value=latest_manual)
    repo_instance.create = AsyncMock()

    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)
    svc._ticket_classifier.decide = AsyncMock()  # should never be called

    session = AsyncMock()
    with patch(
        "src.services.ticket_classification.TicketClassificationAuditsRepository",
        return_value=repo_instance,
    ), patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(),
    ) as get_filter_mock:
        result = await svc.classify_and_store(
            session, ticket=_ticket(), brand=Brand.SUPERSELF,
        )

    assert result.decision == CLASSIFICATION_DECISION_CUSTOMER
    assert result.source == CLASSIFICATION_SOURCE_MANUAL
    assert result.is_service is False
    repo_instance.get_latest_by_ticket.assert_awaited_once_with(_ticket().id)
    repo_instance.create.assert_not_awaited()
    get_filter_mock.assert_not_awaited()
    svc._ticket_classifier.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_and_store_force_overrides_manual() -> None:
    """force=True (admin clicks 'classify now') must bypass the manual guard
    and run the pipeline again, writing a fresh audit row."""
    latest_manual = SimpleNamespace(
        decision=CLASSIFICATION_DECISION_CUSTOMER,
        source=CLASSIFICATION_SOURCE_MANUAL,
        rule=None,
        detail="updated by admin",
        llm_category=None,
        llm_confidence=None,
        threshold=None,
    )
    repo_instance = MagicMock()
    repo_instance.get_latest_by_ticket = AsyncMock(return_value=latest_manual)
    repo_instance.create = AsyncMock()

    flt = MagicMock()
    flt.classify_ticket.return_value = ServiceDecision(
        is_service=True, rule="sender_strict", detail="matched",
    )
    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)

    session = AsyncMock()
    with patch(
        "src.services.ticket_classification.TicketClassificationAuditsRepository",
        return_value=repo_instance,
    ), patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(return_value=flt),
    ):
        result = await svc.classify_and_store(
            session, ticket=_ticket(), brand=Brand.SUPERSELF, force=True,
        )

    assert result.decision == CLASSIFICATION_DECISION_SERVICE
    repo_instance.get_latest_by_ticket.assert_not_awaited()
    repo_instance.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_and_store_writes_audit_when_no_manual_override() -> None:
    """Normal path: no prior manual audit → run classification and persist."""
    repo_instance = MagicMock()
    repo_instance.get_latest_by_ticket = AsyncMock(return_value=None)
    repo_instance.create = AsyncMock()

    flt = MagicMock()
    flt.classify_ticket.return_value = ServiceDecision(
        is_service=False, rule=None, detail=None,
    )
    llm_decision = MagicMock()
    llm_decision.error = None
    llm_decision.is_service = False
    llm_decision.category = MagicMock(value="customer_message")
    llm_decision.confidence = 0.9
    llm_decision.threshold = 0.7

    llm_ctx = MagicMock()
    svc = TicketClassificationService(llm_ctx)
    svc._ticket_classifier.decide = AsyncMock(return_value=llm_decision)

    session = AsyncMock()
    with patch(
        "src.services.ticket_classification.TicketClassificationAuditsRepository",
        return_value=repo_instance,
    ), patch(
        "src.services.ticket_classification.tickets_filter_cache.get_filter",
        new=AsyncMock(return_value=flt),
    ):
        result = await svc.classify_and_store(
            session, ticket=_ticket(), brand=Brand.SUPERSELF,
        )

    assert result.decision == CLASSIFICATION_DECISION_CUSTOMER
    repo_instance.get_latest_by_ticket.assert_awaited_once()
    repo_instance.create.assert_awaited_once()
