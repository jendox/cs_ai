import hashlib
import re
import uuid
from contextlib import contextmanager

import httpx
from pydantic import ValidationError

from db import session_local
from db.repository import Repository
from jobs.models import InitialReplyMessage, JobType
from jobs.rabbitmq_queue import create_job_queue
from libs.zendesk_client.client import ZendeskClient
from libs.zendesk_client.models import Brand, Ticket, Via
from logs.filters import log_ctx
from services import Service

# ----------------------------- Служебный фильтр -----------------------------
_SERVICE_SUBJECT_RE = re.compile(
    r"(?i)\b("
    r"out of office|auto[-\s]?reply|autoreply|automatic reply|delivery status notification|"
    r"undelivered mail|delivery failure|mail delivery failed|read:\s|read receipt|vacation|"
    r"daemon|mailer-daemon|postmaster|no[-\s]?reply|noreply"
    r")\b",
)

_SERVICE_DESC_RE = re.compile(
    r"(?i)\b("
    r"do not reply|this is an automated message|auto[-\s]?generated|system notification|"
    r"mailer-daemon|postmaster|bounce"
    r")\b",
)


def _via_looks_system(via: Via | None = None) -> bool:
    try:
        ch = (via.channel or "").lower() if via else ""
        # эвристики: системные каналы часто api, web_service, rule, trigger
        return ch in {"api", "web_service", "rule", "trigger"}
    except Exception:
        return False


def _is_service_ticket(ticket: Ticket) -> bool:
    """
    Простые эвристики для служебных тикетов.
    При появлении "боевого" фильтра — подмените реализацию внутри.
    """
    subject = (ticket.subject or ticket.raw_subject or "").strip()
    description = (ticket.description or "").strip()
    tags = [s.lower() for s in (ticket.tags or [])]

    # Явные теги
    if any(tag in {"auto_reply", "autoreply", "system", "notification", "mailer-daemon"} for tag in tags):
        return True

    # Паттерны в теме/описании
    if _SERVICE_SUBJECT_RE.search(subject):
        return True
    if _SERVICE_DESC_RE.search(description):
        return True

    # Канал источника
    if _via_looks_system(ticket.via):
        # чтобы не сработало на нормальные интеграции — усилим эвристику:
        if not subject and not description:
            return True

    return False


@contextmanager
def log_context(ticket_id: int, brand: Brand):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.INITIAL_REPLY.value,
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


class InitialReplyWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        brand: Brand,
    ):
        super().__init__(name="initial_reply", brand=brand)
        self._zendesk_client = zendesk_client
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.INITIAL_REPLY,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        message = self._parse_message(payload)
        if not message:
            return True

        ticket = message.ticket
        with log_context(ticket.id, self.brand):
            # Служебные тикеты — выключить наблюдение и завершить
            if _is_service_ticket(ticket):
                return await self._mark_unobserved(ticket)
            # Генерация первичного ответа
            body = self._build_reply_body(ticket)
            if not body:
                return False
            # Отправка ответа в Zendesk + идемпотентность в БД
            channel = "private" if self._zendesk_client.review_mode else "public"
            saved = await self._save_reply(body, ticket.id, channel)
            if not saved:
                return True
            return await self._post_comment(ticket.id, body)

    def _parse_message(self, payload: dict) -> InitialReplyMessage | None:
        try:
            return InitialReplyMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None

    async def _mark_unobserved(self, ticket: Ticket) -> bool:
        try:
            await self._upsert_ticket(ticket, observing=False)
            self.logger.info("ticket.marked_unobserved", extra={"ticket_id": ticket.id})
            return True
        except Exception as exc:
            self.logger.warning("db.update_observing_failed", extra={"ticket_id": ticket.id, "error": str(exc)})
            return False

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
