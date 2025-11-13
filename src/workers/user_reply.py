import hashlib
import uuid
from contextlib import contextmanager

import httpx
from pydantic import ValidationError

from src.db import session_local
from src.db.repository import Repository
from src.jobs.models import JobType, UserReplyMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand, Ticket
from src.logs.filters import log_ctx
from src.services import Service


@contextmanager
def log_context(ticket_id: int, brand: Brand):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.USER_REPLY.value,
        "ticket_id": ticket_id,
        "iteration_id": uuid.uuid4().hex[:8],
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass


class UserReplyWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        brand: Brand,
    ):
        super().__init__(name="user_reply", brand=brand)
        self._zendesk_client = zendesk_client
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.USER_REPLY,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        pass

    def _parse_message(self, payload: dict) -> UserReplyMessage | None:
        try:
            return UserReplyMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None

    def _build_reply_body(self, ticket: Ticket) -> str | None:
        try:
            body = _generate_ai_initial_reply(ticket)
            if not body:
                # Пустой ответ — странно; можно вернуть False для retry или True, чтобы не зациклиться.
                self.logger.warning("ai.empty_body", extra={"ticket_id": ticket.id})
                return None
            return body
        except Exception as exc:
            self.logger.warning("ai.generate_failed", extra={"ticket_id": ticket.id, "error": str(exc)})
            return None

    async def _save_reply(self, body: str, ticket_id: int, channel: str) -> bool:
        body_hash = hashlib.md5(body.encode()).hexdigest()
        async with session_local() as session:
            repo = Repository(session)
            async with session.begin():
                recorded = await repo.record_our_post(
                    ticket_id=ticket_id, body_hash=body_hash, channel=channel,
                )
                if not recorded:
                    # такой ответ уже фиксировали — не дублируем
                    self.logger.info("our_post.duplicate_skip", extra={"ticket_id": ticket_id})
                    return False
        self.logger.info("our_post.reply.saved", extra={"ticket_id": ticket_id})
        return True

    async def _post_comment(self, ticket_id: int, comment: str) -> bool:
        try:
            await self._zendesk_client.add_comment(ticket_id, comment)
            self.logger.info("comment.posted", extra={"ticket_id": ticket_id})
            return True
        except httpx.HTTPError as error:
            self.logger.warning("http_error", extra={"ticket_id": ticket_id, "error": str(error)})
            return False
        except Exception as exc:
            self.logger.error("post_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
            return False


# ----------------------------- Генерация ответа (заглушка AI) -----------------------------
def _generate_ai_initial_reply(ticket: Ticket) -> str:
    """
    Здесь должна быть интеграция с AI/LLM.
    Пока — понятная, безопасная заглушка.
    """
    name = "there"
    subject = (ticket.subject or ticket.raw_subject or "").strip()
    description = (ticket.description or "").strip()

    lines = [f"Hi {name}, thanks for reaching out!"]
    if subject:
        lines.append(subject)
    if description:
        lines.append("")
        lines.append(description)

    lines.append("")
    lines.append("Here’s what we can do next: ")
    lines.append("• I’ll look into this and get back with details.")
    lines.append("")
    lines.append("— Support")

    return "\n".join(lines).strip()
