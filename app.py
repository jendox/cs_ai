import logging.config

import anyio

import config
import services
from db.sa import Database
from libs.zendesk_client.client import create_zendesk_client
from libs.zendesk_client.models import Brand
from logs import LogEnvironment, TelegramHandler, build_logging_config
from workers import InitialReplyWorker
from zendesk.poller import Poller

logger = logging.getLogger("cs")


async def main():
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


if __name__ == "__main__":
    app_settings = config.AppSettings.load()
    config.app_settings.set(app_settings)
    telegram_handler = None
    if app_settings.telegram.enabled:
        telegram_handler = TelegramHandler(
            bot_token=app_settings.telegram.bot_token.get_secret_value(),
            chat_id=app_settings.telegram.chat_id,
            level=app_settings.telegram.min_level,
        )
    log_config = build_logging_config(LogEnvironment.DEV, json_logs=True, telegram_handler=telegram_handler)
    logging.config.dictConfig(log_config)
    anyio.run(main)
