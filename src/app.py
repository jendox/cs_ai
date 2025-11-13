import logging.config

import anyio

from src import config, services
from src.db.sa import Database
from src.libs.zendesk_client.client import create_zendesk_client
from src.libs.zendesk_client.models import Brand
from src.workers import InitialReplyWorker
from src.zendesk.poller import Poller

logger = logging.getLogger("cs")


async def app():
    brand = Brand.SUPERSELF
    logger.info("app.up", extra={"brand": brand.value})
    try:
        settings = config.app_settings.get()
        amqp_url = settings.rabbitmq.amqp_url
        async with (
            Database.lifespan(url=settings.postgres.url),
            create_zendesk_client(settings.zendesk) as zendesk_client,
        ):
            tasks = [
                Poller(zendesk_client, amqp_url, brand),
                InitialReplyWorker(zendesk_client, amqp_url, brand),
                # UserReplyWorker(zendesk_client, amqp_url, brand),
                # AgentDirectiveWorker(zendesk_client, amqp_url, brand),
                # TicketClosedWorker(zendesk_client, amqp_url, brand),
            ]

            async with anyio.create_task_group() as tg:
                for task in tasks:
                    tg.start_soon(services.supervise, task)

    except anyio.get_cancelled_exc_class():
        logger.info("app.cancelled")
    except Exception:
        logger.error("app.fatal", exc_info=True)
    finally:
        logger.info("app.shutdown", extra={"brand": brand.value})
