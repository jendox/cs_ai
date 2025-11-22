import json
import logging
from textwrap import dedent

from src.ai.config import RuntimeResponseSettings
from src.ai.llm_clients import LLMClientInterface
from src.ai.tools.context import LLMContext
from src.ai.utils import extract_json_block
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

    async def _run_agent_loop(
        self,
        client: LLMClientInterface,
        settings: RuntimeResponseSettings,
        session_id: str,
        system_prompt: str,
        user_content: str,
    ) -> str | None:
        messages = [{"content": user_content, "role": "user"}]
        tools = self._llm_context.amazon_executor.tools

        while True:
            raw_text = await client.chat(
                messages=messages,
                settings=settings,
                session_id=session_id,
                system_prompt=system_prompt,
                tools=tools,
            )
            try:
                payload = extract_json_block(raw_text)
                # print(repr(payload))
                data = json.loads(payload)
            except Exception as exc:
                self.logger.warning(
                    "json_parse_failed",
                    extra={"raw": raw_text[:200], "session_id": session_id, "error": str(exc)},
                )
                raise

            tool_call = data.get("tool_call")
            if tool_call:
                name = tool_call["name"]
                args = tool_call.get("arguments", {})
                try:
                    result = await self._llm_context.amazon_executor.execute(name, args)
                except Exception as exc:
                    self.logger.warning(
                        "tool_failed",
                        extra={"tool": name, "session_id": session_id, "error": str(exc)},
                    )
                    raise
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps({
                            "tool": name,
                            "arguments": args,
                            "result": result,
                        }),
                    },
                )
                continue

            body = data.get("body", "")
            if not isinstance(body, str):
                self.logger.warning(
                    "invalid_body",
                    extra={"data": data, "session_id": session_id},
                )
                raise ValueError("Invalid 'body' field")

            return body.strip()

    async def _make_llm_request(
        self,
        client: LLMClientInterface,
        ticket: Ticket,
        settings: RuntimeResponseSettings,
        session_id: str,
    ) -> str:
        content = self._build_initial_reply_message(ticket)
        system_prompt = await self._llm_context.prompt_storage.get_initial_reply(ticket.brand)

        return await self._run_agent_loop(
            client=client,
            settings=settings,
            session_id=session_id,
            system_prompt=system_prompt.text,
            user_content=content,
        )

    async def generate(self, ticket: Ticket, session_id: str) -> str:
        settings = await self._response_settings()
        llm_settings = self._llm_context.client_pool.llm_settings
        provider = settings.provider or llm_settings.default_provider
        model = settings.model or llm_settings.get_provider_settings(provider).model
        cfg = settings.model_copy(update={"model": model})
        client = self._llm_context.client_pool.get_client(provider)

        return await self._make_llm_request(client, ticket, cfg, session_id)
