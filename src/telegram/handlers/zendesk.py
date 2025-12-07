import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import PostChannel, UserRole
from src.db.repositories import ZendeskRuntimeSettingsRepository
from src.telegram.context import log_context
from src.telegram.decorators import with_repository
from src.telegram.filters import TWO_PARAMS_PARTS_COUNT, RoleRequired

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


def _format_channel(channel: PostChannel) -> str:
    return {
        PostChannel.INTERNAL: "internal (внутренние комментарии)",
        PostChannel.PUBLIC: "public (публичные комментарии)",
    }[channel]


@router.message(Command("zendesk_mode"), RoleRequired(UserRole.ADMIN))
@with_repository(ZendeskRuntimeSettingsRepository)
async def cmd_zendesk_mode(
    message: Message,
    repo: ZendeskRuntimeSettingsRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        channel = await repo.get_channel()
        await message.answer(
            "ℹ️ Текущий режим комментариев в Zendesk:\n\n"
            f"<b>review_mode</b>: <code>{channel.value}</code>\n"
            f"{_format_channel(channel)}\n\n"
            "Изменить: <code>/zendesk_mode_set internal</code> или <code>/zendesk_mode_set public</code>",
        )


@router.message(Command("zendesk_mode_set"), RoleRequired(UserRole.ADMIN))
@with_repository(ZendeskRuntimeSettingsRepository)
async def cmd_zendesk_mode_set(
    message: Message,
    repo: ZendeskRuntimeSettingsRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/zendesk_mode_set &lt;internal|public&gt;</code>",
            )
            return

        raw_mode = parts[1].strip().lower()
        if raw_mode not in {"internal", "public"}:
            await message.answer(
                "Неизвестный режим.\n"
                "Допустимые значения: <code>internal</code>, <code>public</code>",
            )
            return

        new_channel = PostChannel(raw_mode)

        current = await repo.get_channel()
        if current == new_channel:
            await message.answer(
                f"ℹ️ Режим уже установлен: <b>{new_channel.value}</b>.",
            )
            return

        updated_by = message.from_user.username or str(message.from_user.id)
        try:
            await repo.set_channel(new_channel, updated_by=updated_by)
            logger.info(
                "zendesk_mode_set.success",
                extra={
                    "new_channel": new_channel.value,
                    "updated_by": updated_by,
                },
            )
            await message.answer(
                "✅ Режим комментариев в Zendesk обновлён:\n\n"
                f"<b>review_mode</b>: <code>{new_channel.value}</code>",
            )
        except Exception as exc:
            logger.error(
                "zendesk_mode_set.error",
                extra={
                    "new_channel": new_channel.value,
                    "error": str(exc),
                },
            )
            await message.answer(
                "⚠️ Ошибка при смене режима комментариев. Подробности в логах.",
            )
