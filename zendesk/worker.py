import logging
import os

import anyio

from db import SessionLocal
from db.repository import Repository
from jobs.rabbitmq_queue import RabbitJobQueue
from libs.zendesk_client.client import ZendeskClient


class Worker:
    def __init__(self, client: ZendeskClient):
        self.client = client
        self.repo: Repository | None = None
        self.job_queue: RabbitJobQueue | None = None
        self.logger = logging.getLogger("zendesk_worker")

    async def _process_initial_reply(self) -> None:
        self.logger.info("job processing-ticket: %s", 1)

    # async def worker_loop(self) -> None:
    #     if job_entity is None:
    #         return
    #     try:
    #         if job.job_type == JobType.INITIAL_REPLY:
    #             await self._process_initial_reply(job)
    #         elif job.job_type == JobType.USER_REPLY:
    #             self.logger.info("job_type=user_reply:ticket_id=%d", job.ticket_id)
    #         elif job.job_type == JobType.AGENT_DIRECTIVE:
    #             self.logger.info("job_type=agen_directive:ticket_id=%d", job.ticket_id)
    #         elif job.job_type == JobType.TICKET_CLOSED:
    #             self.logger.info("job_type=ticket_closed:ticket_id=%d", job.ticket_id)
    #         else:
    #             self.logger.warning("Unsupported job_type: %s", job.job_type)
    #     except TransientError as e:
    #         await repo.fail_job(job.id, retry=True)  # backoff внутри
    #     except Exception as exc:
    #         pass

    async def start(self) -> None:
        self.logger.info("worker:%d is starting", os.getgid())
        self.job_queue = RabbitJobQueue()
        await self.job_queue.ensure()
        while True:
            try:
                async with SessionLocal() as session:
                    self.repo = Repository(session)
                    async with session.begin():
                        await self.worker_loop()
            finally:
                self.repo = None
                await anyio.sleep(1.0)
