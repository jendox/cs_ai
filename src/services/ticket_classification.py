from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.context import LLMContext
from src.ai.ticket_classifier import LLMTicketClassifier
from src.db.repositories import (
    CLASSIFICATION_DECISION_CUSTOMER,
    CLASSIFICATION_DECISION_SERVICE,
    CLASSIFICATION_DECISION_UNKNOWN,
    CLASSIFICATION_SOURCE_LLM,
    CLASSIFICATION_SOURCE_RULE,
    TicketClassificationAuditCreate,
    TicketClassificationAuditsRepository,
)
from src.libs.zendesk_client.models import Brand, Ticket
from src.tickets_filter.cache import tickets_filter_cache


@dataclass(frozen=True)
class TicketClassificationResult:
    is_service: bool
    decision: str
    source: str
    rule: str | None = None
    detail: str | None = None
    llm_category: str | None = None
    llm_confidence: float | None = None
    threshold: float | None = None


class TicketClassificationService:
    def __init__(self, llm_context: LLMContext) -> None:
        self._ticket_classifier = LLMTicketClassifier(llm_context)

    async def classify_and_store(
        self,
        session: AsyncSession,
        *,
        ticket: Ticket,
        brand: Brand,
    ) -> TicketClassificationResult:
        result = await self.classify(session, ticket=ticket, brand=brand)
        await TicketClassificationAuditsRepository(session).create(
            TicketClassificationAuditCreate(
                ticket_id=ticket.id,
                brand_id=brand.value,
                decision=result.decision,
                source=result.source,
                rule=result.rule,
                detail=result.detail,
                llm_category=result.llm_category,
                llm_confidence=result.llm_confidence,
                threshold=result.threshold,
            ),
        )
        return result

    async def classify(
        self,
        session: AsyncSession,
        *,
        ticket: Ticket,
        brand: Brand,
    ) -> TicketClassificationResult:
        tickets_filter = await tickets_filter_cache.get_filter(session, brand)
        rule_decision = tickets_filter.classify_ticket(ticket)
        if rule_decision.is_service:
            return TicketClassificationResult(
                is_service=True,
                decision=CLASSIFICATION_DECISION_SERVICE,
                source=CLASSIFICATION_SOURCE_RULE,
                rule=rule_decision.rule,
                detail=rule_decision.detail,
            )
        if rule_decision.rule is not None:
            return TicketClassificationResult(
                is_service=False,
                decision=CLASSIFICATION_DECISION_CUSTOMER,
                source=CLASSIFICATION_SOURCE_RULE,
                rule=rule_decision.rule,
                detail=rule_decision.detail,
            )

        llm_decision = await self._ticket_classifier.decide(ticket)
        if llm_decision.error is not None:
            return TicketClassificationResult(
                is_service=False,
                decision=CLASSIFICATION_DECISION_UNKNOWN,
                source=CLASSIFICATION_SOURCE_LLM,
                detail=llm_decision.error,
                llm_category=llm_decision.category.value,
                llm_confidence=llm_decision.confidence,
                threshold=llm_decision.threshold,
            )

        decision = (
            CLASSIFICATION_DECISION_SERVICE
            if llm_decision.is_service
            else CLASSIFICATION_DECISION_CUSTOMER
        )
        return TicketClassificationResult(
            is_service=llm_decision.is_service,
            decision=decision,
            source=CLASSIFICATION_SOURCE_LLM,
            llm_category=llm_decision.category.value,
            llm_confidence=llm_decision.confidence,
            threshold=llm_decision.threshold,
        )
