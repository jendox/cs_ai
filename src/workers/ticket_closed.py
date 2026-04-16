import uuid
from typing import cast

from pydantic import ValidationError

from src.db import session_local
from src.db.repositories import TicketsRepository
from src.jobs.models import JobType, TicketClosedMessage
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand
from src.services import Service

from .log_context import log_context


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
        self.brand = cast(Brand, self.brand)

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
        iteration_id = uuid.uuid4().hex[:8]
        async with log_context(ticket_id, self.brand, iteration_id, JobType.TICKET_CLOSED):
            async with session_local() as session:
                repo = TicketsRepository(session)
                async with session.begin():
                    try:
                        await repo.set_observing(ticket_id, observing=False)
                        self.logger.info("ticket.marked_unobserved")
                        return True
                    except Exception as exc:
                        self.logger.warning(
                            "db.update_observing_failed",
                            extra={"error": str(exc)},
                        )
                        return False

    def _parse_message(self, payload: dict) -> TicketClosedMessage | None:
        try:
            return TicketClosedMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None
