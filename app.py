import logging.config

import anyio

import config
from libs.zendesk_client.client import create_zendesk_client
from libs.zendesk_client.models import Brand
from logs.setup import build_logging_config
from logs.telegram import TelegramHandler
from zendesk import Poller

logger = logging.getLogger(__name__)


async def main():
    # ticket 109793
    try:
        settings = config.app_settings.get()
        logger.info("app.start", extra={"brand": Brand.SUPERSELF.value})
        async with create_zendesk_client(settings.zendesk) as client:
            poller = Poller(client, Brand.SUPERSELF)

            async with anyio.create_task_group() as tg:
                tg.start_soon(poller.start, settings.rabbitmq.amqp_url)

    except anyio.get_cancelled_exc_class():
        logger.info("Прервано пользователем")
    except Exception as exc:
        logger.error(exc, exc_info=True)


if __name__ == "__main__":
    app_settings = config.AppSettings.load()
    config.app_settings.set(app_settings)
    tg_handler = None
    if app_settings.telegram.enabled:
        tg_handler = TelegramHandler(
            bot_token=app_settings.telegram.bot_token.get_secret_value(),
            chat_id=app_settings.telegram.chat_id,
            level=app_settings.telegram.min_level,
        )
    logging.config.dictConfig(build_logging_config(env="prod", json_logs=True, telegram_handler=tg_handler))
    anyio.run(main)
