import json
import logging
from textwrap import dedent

from src.ai.config import LLMRuntimeSettingsStorage, RuntimeResponseSettings
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients import LLMClientInterface, LLMClientPool
from src.ai.utils import extract_json_block
from src.libs.zendesk_client.models import Ticket


class LLMReplyGenerator:
    def __init__(
        self,
        client_pool: LLMClientPool,
        settings_storage: LLMRuntimeSettingsStorage,
        prompt_storage: LLMPromptStorage,
    ) -> None:
        self._client_pool = client_pool
        self._settings_storage = settings_storage
        self._prompt_storage = prompt_storage
        self.logger = logging.getLogger("llm_reply_generator")

    async def _response_settings(self) -> RuntimeResponseSettings:
        return await self._settings_storage.get_response()

    @staticmethod
    def _build_initial_reply_message(ticket: Ticket) -> str:
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

            [CONTEXT]
            - This is the very first customer message.
            - No previous messages or replies exist.
            - Write the first helpful and friendly response.
        """)

    async def _make_llm_request(
        self,
        client: LLMClientInterface,
        ticket: Ticket,
        settings: RuntimeResponseSettings,
        session_id: str,
    ) -> str:
        try:
            content = self._build_initial_reply_message(ticket)
            system_prompt = await self._prompt_storage.get_initial_reply(ticket.brand)
            text = await client.chat(
                messages=[{"content": content, "role": "user"}],
                settings=settings,
                session_id=session_id,
                system_prompt=system_prompt.text,
            )
            data = json.loads(extract_json_block(text))
            body = data.get("body")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("Invalid or empty 'body' field")

            return body.strip()

        except Exception as exc:
            self.logger.warning(
                "llm_generate.error",
                extra={
                    "session_id": session_id,
                    "error": str(exc),
                    "raw_text": text[:200],
                },
            )
            return ""

    async def generate(self, ticket: Ticket, session_id: str) -> str:
        settings = await self._response_settings()
        llm_settings = self._client_pool.llm_settings
        provider = settings.provider or llm_settings.default_provider
        model = settings.model or llm_settings.get_provider_settings(provider).model
        cfg = settings.model_copy(update={"model": model})
        client = self._client_pool.get_client(provider)

        return await self._make_llm_request(client, ticket, cfg, session_id)
