import json
import logging
from dataclasses import dataclass
from enum import Enum
from textwrap import dedent

from pydantic import BaseModel, Field

from src.ai.config import LLMRuntimeSettingsStorage, RuntimeClassificationSettings
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients.interfaces import LLMClientInterface
from src.ai.llm_clients.pool import LLMClientPool
from src.ai.utils import extract_json_block
from src.libs.zendesk_client.models import Brand, Ticket


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


class LLMTicketClassifier:
    def __init__(
        self,
        client_pool: LLMClientPool,
        settings_storage: LLMRuntimeSettingsStorage,
        prompt_storage: LLMPromptStorage,
    ) -> None:
        self._client_pool = client_pool
        self._settings_storage = settings_storage
        self._prompt_storage = prompt_storage
        self.logger = logging.getLogger("llm_ticket_classifier")

    async def _classification_settings(self) -> RuntimeClassificationSettings:
        return await self._settings_storage.get_classification()

    async def _build_classification_prompt(self, brand: Brand) -> str:
        prompt_template = await self._prompt_storage.get_classification(brand)
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
        session_id: str,
    ) -> MessageClassification:
        content = self._build_classification_message(ticket)
        try:
            system_prompt = await self._build_classification_prompt(ticket.brand)
            text = await client.chat(
                messages=[{"content": content, "role": "user"}],
                settings=settings,
                session_id=session_id,
                system_prompt=system_prompt,
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
