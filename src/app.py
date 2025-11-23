import logging.config

import anyio

from src import config, services
from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients.pool import LLMClientPool
from src.ai.tools.context import LLMContext
from src.ai.tools.executor import AmazonToolExecutor
from src.db.sa import Database
from src.libs.amazon_client.client import AsyncAmazonClient
from src.libs.amazon_client.enums import HTTPX_MAX_CONNECTIONS, EndpointRegion
from src.libs.zendesk_client.client import create_zendesk_client
from src.libs.zendesk_client.models import Brand
from src.workers import InitialReplyWorker, TicketClosedWorker
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
            AsyncAmazonClient.setup(
                settings=settings.amazon,
                max_connection=HTTPX_MAX_CONNECTIONS,
                region=EndpointRegion.EU,
            ),
        ):
            amazon_client = AsyncAmazonClient.get_initialized_instance()

            llm_context = LLMContext(
                client_pool=LLMClientPool(settings.llm),
                runtime_storage=LLMRuntimeSettingsStorage(),
                prompt_storage=LLMPromptStorage(),
                amazon_executor=AmazonToolExecutor(amazon_client),
            )
            tasks = [
                Poller(zendesk_client, amqp_url, brand),
                InitialReplyWorker(zendesk_client, amqp_url, llm_context, brand),
                # UserReplyWorker(zendesk_client, amqp_url, brand),
                # AgentDirectiveWorker(zendesk_client, amqp_url, brand),
                TicketClosedWorker(zendesk_client, amqp_url, brand),
                # TelegramAdmin(settings.telegram, brand),
            ]
            # синхронизация каталога один раз при запуске приложения, т.к. бд пустая
            # дальше нужно запускать периодически через админку, т.к. данные меняются редко
            # await catalog_sync.sync_catalog_for_brand_all_eu_markets(brand.value, amazon_client)

            async with anyio.create_task_group() as tg:
                for task in tasks:
                    tg.start_soon(services.supervise, task)

    except anyio.get_cancelled_exc_class():
        logger.info("app.cancelled")
    except Exception:
        logger.error("app.fatal", exc_info=True)
    finally:
        logger.info("app.shutdown", extra={"brand": brand.value})
