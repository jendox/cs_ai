import uuid
from contextlib import asynccontextmanager

from pydantic import ValidationError

from src.db import session_local
from src.db.repositories import TicketsRepository
from src.jobs.models import JobType, TicketClosedMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand
from src.logs.filters import log_ctx
from src.services import Service


@asynccontextmanager
async def log_context(ticket_id: int, brand: Brand):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.TICKET_CLOSED.value,
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


class TicketClosedWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        brand: Brand,
    ) -> None:
        super().__init__(name="ticket_closed", brand=brand)
        self._zendesk_client = zendesk_client
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.TICKET_CLOSED,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        message = self._parse_message(payload)
        if not message:
            return True

        ticket_id = message.ticket_id
        async with log_context(ticket_id=ticket_id, brand=self.brand):
            async with session_local() as session:
                repo = TicketsRepository(session)
                async with session.begin():
                    try:
                        await repo.mark_unobserved(ticket_id)
                        self.logger.info(
                            "ticket.marked_unobserved",
                            extra={"ticket_id": ticket_id},
                        )
                        return True
                    except Exception as exc:
                        self.logger.warning(
                            "db.update_observing_failed",
                            extra={"ticket_id": ticket_id, "error": str(exc)},
                        )
                        return False

    def _parse_message(self, payload: dict) -> TicketClosedMessage | None:
        try:
            return TicketClosedMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None
