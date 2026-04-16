import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from textwrap import dedent

from pydantic import BaseModel, Field

from src.ai import utils
from src.ai.config import RuntimeClassificationSettings
from src.ai.context import LLMContext
from src.ai.llm_clients.interfaces import LLMClientInterface
from src.ai.utils import extract_json_block
from src.libs.zendesk_client.models import Brand, Ticket


class MessageCategory(StrEnum):
    CUSTOMER_SUPPORT = "customer_support"
    MARKETING_OR_SPAM = "marketing_or_spam"

    @classmethod
    def all(cls) -> list[str]:
        return list(cls)

    @classmethod
    def for_prompt(cls) -> str:
        return " | ".join(f'"{value}"' for value in cls.all())


class MessageClassification(BaseModel):
    category: MessageCategory
    confidence: float = Field(..., ge=0.0, le=1.0)


@dataclass(frozen=True)
class LLMTicketDecision:
    is_service: bool
    category: MessageCategory
    confidence: float
    threshold: float
    error: str | None = None


@dataclass(frozen=True)
class LLMClassificationResult:
    classification: MessageClassification | None
    error: str | None = None


class LLMTicketClassifier:
    def __init__(self, llm_context: LLMContext) -> None:
        self._llm_context = llm_context
        self.logger = logging.getLogger("llm_ticket_classifier")

    async def _classification_settings(self) -> RuntimeClassificationSettings:
        return await self._llm_context.runtime_storage.get_classification()

    async def _build_classification_prompt(self, brand: Brand) -> str:
        prompt_template = await self._llm_context.prompt_storage.classification_prompt(brand)
        return prompt_template.text.format(
            customer_support=MessageCategory.CUSTOMER_SUPPORT.value,
            marketing_or_spam=MessageCategory.MARKETING_OR_SPAM.value,
            categories_for_prompt=MessageCategory.for_prompt(),
        )

    @staticmethod
    def _build_classification_message(ticket: Ticket) -> str:
        via_channel = ticket.via.channel if ticket.via and ticket.via.channel else "unknown"
        sender = ticket.via.source.from_ if ticket.via and ticket.via.source else None
        sender_email = sender.address if sender else ""
        sender_name = sender.name if sender else ""

        subject = ticket.subject or ticket.raw_subject
        body = (ticket.description or "").strip()
        return dedent(f"""
                [TICKET]
                id: {ticket.id}
                brand: {ticket.brand.name if ticket.brand else ""}
                channel: {via_channel}
                created_at: {ticket.created_at}

                [CUSTOMER]
                email: {sender_email}
                name: {sender_name}

                [MESSAGE]
                subject:
                {subject}

                body:
                {body}
            """)

    async def _classify(
        self,
        client: LLMClientInterface,
        ticket: Ticket,
        settings: RuntimeClassificationSettings,
    ) -> LLMClassificationResult:
        content = self._build_classification_message(ticket)
        try:
            system_prompt = await self._build_classification_prompt(ticket.brand)
            text = await client.chat(
                messages=[{"content": content, "role": "user"}],
                settings=settings,
                system_prompt=system_prompt,
            )
            if not text.strip():
                raise ValueError("Empty LLM classification response")
            data = json.loads(extract_json_block(text))
            return LLMClassificationResult(classification=MessageClassification(**data))
        except Exception as exc:
            error = str(exc)
            self.logger.warning("llm_classify.error: %s", error, extra={"error": error})
            return LLMClassificationResult(classification=None, error=error)

    async def decide(self, ticket: Ticket) -> LLMTicketDecision:
        settings = await self._classification_settings()
        if not settings.enabled:
            self.logger.info("settings.disabled")
            return LLMTicketDecision(
                is_service=False,
                category=MessageCategory.CUSTOMER_SUPPORT,
                confidence=0.0,
                threshold=settings.threshold,
            )
        client, cfg = utils.resolve_llm_client_and_cfg(self._llm_context, settings)
        result = await self._classify(client, ticket, cfg)
        if result.classification is None:
            return LLMTicketDecision(
                is_service=False,
                category=MessageCategory.CUSTOMER_SUPPORT,
                confidence=0.0,
                threshold=settings.threshold,
                error=result.error or "LLM classification failed",
            )

        classification = result.classification
        is_service = (
            classification.category is MessageCategory.MARKETING_OR_SPAM
            and classification.confidence >= settings.threshold
        )
        self.logger.info(
            "decision",
            extra={
                "category": classification.category.value,
                "confidence": f"{classification.confidence:.3f}",
                "threshold": settings.threshold,
            },
        )
        return LLMTicketDecision(
            is_service=is_service,
            category=classification.category,
            confidence=classification.confidence,
            threshold=settings.threshold,
        )
