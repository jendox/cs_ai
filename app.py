import logging

import anyio
from dotenv import load_dotenv

from libs.zendesk_client.client import ZendeskSettings, create_zendesk_client
from libs.zendesk_client.models import Brand
from zendesk_poller import Poller

LOGGER_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logger = logging.getLogger(__name__)


async def main():
    # ticket 109793
    load_dotenv()
    try:
        async with create_zendesk_client(ZendeskSettings.load()) as client:
            poller = Poller(client, Brand.HIPCRATE)
            await poller.start_polling()
    except anyio.get_cancelled_exc_class():
        logger.info("Прервано пользователем")
    except Exception as exc:
        logger.error(exc, exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(
        format=LOGGER_FORMAT,
        level=logging.INFO,
    )
    anyio.run(main)
