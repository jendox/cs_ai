import logging

import anyio
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.libs.amazon_client.client import AsyncAmazonClient
from src.libs.zendesk_client.models import Brand
from src.telegram.filters import RoleRequired
from src.workflows.catalog_sync import sync_catalog_for_brand_all_eu_markets

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


@router.message(Command("sync_catalog"), RoleRequired(UserRole.ADMIN))
async def sync_catalog(message: Message):
    brand = Brand.SUPERSELF
    await message.answer(f"{brand.name} catalog synchronization started...")
    logger.info("sync_catalog.started", extra={"brand": brand.value})

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            sync_catalog_for_brand_all_eu_markets,
            brand,
            AsyncAmazonClient.get_initialized_instance(),
        )
