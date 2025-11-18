import json
import logging
from dataclasses import dataclass
from enum import Enum
from textwrap import dedent

from pydantic import BaseModel, Field

from src.ai.config import LLMRuntimeSettingsStorage, RuntimeClassificationSettings
from src.ai.llm_clients.interfaces import LLMClientInterface
from src.ai.llm_clients.pool import LLMClientPool
from src.ai.utils import extract_json_block
from src.libs.zendesk_client.models import Ticket


class MessageCategory(str, Enum):
    CUSTOMER_SUPPORT = "customer_support"
    MARKETING_OR_SPAM = "marketing_or_spam"

    @classmethod
    def all(cls) -> list[str]:
        return [member.value for member in cls]

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


def _build_classification_prompt() -> str:
    """
    System prompt for classifying tickets as customer_support vs marketing_or_spam.
    """
    return dedent(f"""
        You are an AI classifier for an ecommerce customer support inbox.
        DO NOT generate replies to the user. Your job is ONLY classification.

        Your task:
        1. Determine whether the incoming message was written by:
           - a real customer or potential customer, OR
           - a marketing/agency/sales/spam sender.
        2. Output a strict JSON object ONLY.

        Classification rules:
        - "{MessageCategory.CUSTOMER_SUPPORT.value}": questions, complaints, order issues, product questions,
          returns, refunds, delivery problems, missing items, product feedback, pre-purchase questions.
        - "{MessageCategory.MARKETING_OR_SPAM.value}": outreach messages, agencies, advertising offers, SEO,
          backlinks, brand deals, collaboration proposals, bulk manufacturing, wholesale offers,
          lead generation, guest posts, crypto, financing, B2B sales outreach, supplier offers.

        Useful indicators of marketing/spam (not required but helpful):
        - Personal email domains used for outreach (gmail/outlook/yahoo etc.)
        - Corporate outreach keywords: "collaboration", "agency", "SEO", "backlinks", "sponsorship", "private label",
          "wholesale", "bulk", "manufacturing", "guest post", "traffic", "marketing", "advertising", "proposal"
        - Messages that do NOT reference an actual order, product issue, delivery, or customer problem.

        Output rules:
        - You MUST respond with VALID JSON ONLY.
        - No explanation or text outside JSON.
        - JSON schema:

        {{
            "category": {MessageCategory.for_prompt()},
            "confidence": float
        }}

        Where:
        - confidence (number between 0.0 and 1.0) indicates how sure you are in the classification.
        - If unsure, choose the most probable class and set lower confidence.

        Do not include any other fields.
        Do not add comments.
        Do not generate customer replies.
    """).strip()


CLASSIFICATION_PROMPT = _build_classification_prompt()


class LLMTicketClassifier:
    def __init__(
        self,
        client_pool: LLMClientPool,
        settings_storage: LLMRuntimeSettingsStorage,
    ) -> None:
        self._client_pool = client_pool
        self._settings_storage = settings_storage
        self.logger = logging.getLogger("llm_ticket_classifier")

    async def _classification_settings(self) -> RuntimeClassificationSettings:
        return await self._settings_storage.get_classification()

    async def _classify(
        self,
        client: LLMClientInterface,
        ticket: Ticket,
        settings: RuntimeClassificationSettings,
        session_id: str,
    ) -> MessageClassification:
        content = f"#{ticket.id}\nSubject:\n{ticket.subject}\nDescription:\n{ticket.description}"
        try:
            text = await client.chat(
                messages=[{"content": content, "role": "user"}],
                settings=settings,
                session_id=session_id,
                system_prompt=CLASSIFICATION_PROMPT,
            )
            data = json.loads(extract_json_block(text))
            return MessageClassification(**data)
        except Exception as exc:
            self.logger.warning(
                "llm_classify.error",
                extra={
                    "session_id": session_id,
                    "error": str(exc),
                },
            )
            return MessageClassification(
                category=MessageCategory.CUSTOMER_SUPPORT,
                confidence=0.0,
            )

    async def decide(self, ticket: Ticket, session_id: str) -> LLMTicketDecision:
        settings = await self._classification_settings()
        if not settings.enabled:
            self.logger.info("settings.disabled")
            return LLMTicketDecision(
                is_service=False,
                category=MessageCategory.CUSTOMER_SUPPORT,
                confidence=0.0,
            )
        llm_settings = self._client_pool.llm_settings
        provider = settings.provider or llm_settings.default_provider
        model = settings.model or llm_settings.get_provider_settings(provider).model
        cfg = settings.model_copy(update={"model": model})
        client = self._client_pool.get_client(provider)
        classification = await self._classify(client, ticket, cfg, session_id)
        is_service = (
            classification.category is MessageCategory.MARKETING_OR_SPAM
            and classification.confidence >= settings.threshold
        )
        self.logger.info(
            "decision",
            extra={
                "category": classification.category.value,
                "confidence": f"{classification.confidence:.3f}",
                "provider": provider.value,
                "model": model,
                "threshold": settings.threshold,
            },
        )
        return LLMTicketDecision(
            is_service=is_service,
            category=classification.category,
            confidence=classification.confidence,
        )
