import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.ai.context import LLMContext
from src.db.models import UserRole
from src.telegram.context import log_context
from src.telegram.filters import TWO_PARAMS_PARTS_COUNT, RoleRequired
from src.telegram.prompt_parsing import allowed_brand_tokens, parse_brand_token
from src.workflows.catalog_sync import sync_catalog_for_brand_all_eu_markets

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


@router.message(Command("sync_catalog"), RoleRequired(UserRole.ADMIN))
async def sync_catalog(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/sync_catalog &lt;brand&gt;</code>",
            )
            return

        brand_token = parts[1]
        brand = parse_brand_token(brand_token)
        if brand is None:
            allowed = ", ".join(allowed_brand_tokens())
            await message.answer(
                "Неизвестный бренд.\n"
                f"Допустимые значения: <code>{allowed}</code>",
            )
            return

        await message.answer(f"Начата синхронизация каталога для {brand.name}...")
        logger.info("sync_catalog.started", extra={"brand": brand.value})

        try:
            await sync_catalog_for_brand_all_eu_markets(brand, llm_context.amazon_mcp_client)
        except Exception as exc:
            logger.exception(
                "sync_catalog.failed",
                extra={"brand": brand.value, "error": str(exc)},
            )
            await message.answer("⚠️ Произошла ошибка при синхронизации каталога. Подробности в логах.")
        else:
            await message.answer("✅ Синхронизация каталога завершена.")
