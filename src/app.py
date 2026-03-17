import logging.config
from typing import Any

import anyio

from src import config, services
from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.context import LLMContext
from src.ai.llm_clients.pool import LLMClientPool
from src.db.sa import Database
from src.libs.zendesk_client.client import create_zendesk_client
from src.libs.zendesk_client.models import Brand
from src.telegram.admin import TelegramAdmin
from src.workers import FollowUpReplyWorker, InitialReplyWorker, TicketClosedWorker
from src.workflows import catalog_sync
from src.zendesk.poller import Poller

logger = logging.getLogger("cs")


async def app():
    logger.info("app.up")
    try:
        settings = config.app_settings.get()
        amqp_url = settings.rabbitmq.amqp_url
        async with (
            Database.lifespan(url=settings.postgres.url),
            AmazonMCPHttpClient.setup(settings.mcp.url),
            create_zendesk_client(settings.zendesk) as zendesk_client,
        ):
            amazon_mcp_client = AmazonMCPHttpClient.get_initialized_instance()

            llm_context = LLMContext(
                client_pool=LLMClientPool(settings.llm),
                runtime_storage=LLMRuntimeSettingsStorage(),
                prompt_storage=LLMPromptStorage(),
                amazon_mcp_client=amazon_mcp_client,
            )

            tasks: list[Any] = []
            for brand in Brand.supported():
                tasks += [
                    Poller(zendesk_client, amqp_url, brand),
                    InitialReplyWorker(zendesk_client, amqp_url, llm_context, brand),
                    FollowUpReplyWorker(zendesk_client, amqp_url, llm_context, brand),
                    # AgentDirectiveWorker(zendesk_client, amqp_url, brand),
                    TicketClosedWorker(zendesk_client, amqp_url, brand),
                ]
                # синхронизация каталога один раз при запуске приложения, т.к. бд пустая
                # дальше нужно запускать периодически через админку, т.к. данные меняются редко
                if settings.init_ref_update:
                    await catalog_sync.sync_catalog_for_brand_all_eu_markets(brand, amazon_mcp_client)

            tasks.append(TelegramAdmin(settings.telegram, llm_context))

            async with anyio.create_task_group() as tg:
                for task in tasks:
                    tg.start_soon(services.supervise, task)

    except anyio.get_cancelled_exc_class():
        logger.info("app.cancelled")
    except Exception:
        logger.error("app.fatal", exc_info=True)
    finally:
        logger.info("app.shutdown")
