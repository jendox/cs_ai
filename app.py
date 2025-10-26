import logging

import anyio
from dotenv import load_dotenv

from libs.zendesk_client.client import ZendeskSettings, create_zendesk_client
from libs.zendesk_client.models import Brand
from zendesk import Poller

LOGGER_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logger = logging.getLogger(__name__)


async def test_method():
    async with create_zendesk_client(ZendeskSettings.load()) as client:
        tickets = await client.test_get_tickets()
        print(tickets)

    raise RuntimeError()


async def main():
    # ticket 109793
    load_dotenv()
    # await test_method()
    try:
        async with create_zendesk_client(ZendeskSettings.load()) as client:
            poller = Poller(client, Brand.SUPERSELF)
            # worker = Worker(client)

            async with anyio.create_task_group() as tg:
                tg.start_soon(poller.start)
                # tg.start_soon(worker.start)

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
