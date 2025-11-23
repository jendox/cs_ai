import logging
from textwrap import dedent

from src.ai import utils
from src.ai.config import RuntimeResponseSettings
from src.ai.context import LLMContext
from src.ai.llm_clients import LLMClientInterface
from src.ai.tools import amazon_tools
from src.libs.zendesk_client.models import Ticket


class LLMReplyGenerator:
    def __init__(self, llm_context: LLMContext) -> None:
        self._llm_context = llm_context
        self.logger = logging.getLogger("llm_reply_generator")

    async def _response_settings(self) -> RuntimeResponseSettings:
        return await self._llm_context.runtime_storage.get_response()

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
    ) -> str:
        content = self._build_initial_reply_message(ticket)
        system_prompt = await self._llm_context.prompt_storage.get_initial_reply(ticket.brand)

        messages = [{"content": content, "role": "user"}]

        try:
            text = await client.chat(
                messages=messages,
                settings=settings,
                system_prompt=system_prompt.text,
                tools=amazon_tools,
            )
            self.logger.info("make_llm_request.success", extra={"text": text[:200]})
            return text
        except Exception as exc:
            self.logger.warning("make_llm_request.failed", extra={"error": str(exc)})
            return ""

    async def generate(self, ticket: Ticket) -> str:
        settings = await self._response_settings()
        client, cfg = utils.resolve_llm_client_and_cfg(self._llm_context, settings)

        return await self._make_llm_request(client, ticket, cfg)
