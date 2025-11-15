import uuid
from contextlib import contextmanager

from pydantic import ValidationError

from src.jobs.models import AgentDirectiveMessage, JobType
from src.jobs.rabbitmq_queue import create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand
from src.logs.filters import log_ctx
from src.services import Service


@contextmanager
def log_context(ticket_id: int, brand: Brand):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": JobType.AGENT_DIRECTIVE.value,
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


class AgentDirectiveWorker(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        brand: Brand,
    ) -> None:
        super().__init__(name="agent_directive", brand=brand)
        self._zendesk_client = zendesk_client
        self._amqp_url = amqp_url

    async def run(self) -> None:
        job_queue = await create_job_queue(self._amqp_url, self.brand)

        await job_queue.consume(
            JobType.AGENT_DIRECTIVE,
            handler=self._handler,
            brand=self.brand,
            prefetch=2,
        )

    async def _handler(self, payload: dict) -> bool:
        pass

    def _parse_message(self, payload: dict) -> AgentDirectiveMessage | None:
        try:
            return AgentDirectiveMessage.model_validate(payload)
        except ValidationError as error:
            self.logger.error("payload.validation.error", extra={"error": str(error)})
            return None
