import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.db.repositories.telegram import TelegramUsersRepository
from src.telegram.context import log_context
from src.telegram.decorators import with_repository
from src.telegram.filters import RoleRequired, TicketId

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


@router.message(Command("add_user"), TicketId(), RoleRequired(UserRole.ADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_add_user(
    message: Message,
    telegram_id: int,
    username: str,
    repo: TelegramUsersRepository,
):
    async with log_context(telegram_id=message.from_user.id):
        try:
            extra = {"username": username, "telegram_id": telegram_id, "role": UserRole.USER.value}
            await repo.create(telegram_id=telegram_id, username=username)
            await message.answer(
                f"Добавлен новый пользователь:\n\n"
                f"<b>username</b>: {username}\n"
                f"<b>telegram_id</b>: {telegram_id}\n"
                f"<b>user_role</b>: {UserRole.USER.name.lower()}",
            )
            logger.info("add_user.success", extra=extra)
        except Exception as exc:
            logger.error("add_user.error", extra={**extra, "error": str(exc)})


@router.message(Command("add_admin"), TicketId(), RoleRequired(UserRole.SUPERADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_add_admin(
    message: Message,
    telegram_id: int,
    username: str,
    repo: TelegramUsersRepository,
):
    async with log_context(telegram_id=message.from_user.id):
        try:
            extra = {"username": username, "telegram_id": telegram_id, "role": UserRole.ADMIN.value}
            await repo.create(telegram_id=telegram_id, username=username)
            await message.answer(
                f"Добавлен новый администратор:\n\n"
                f"<b>username</b>: {username}\n"
                f"<b>telegram_id</b>: {telegram_id}\n"
                f"<b>user_role</b>: {UserRole.ADMIN.name.lower()}",
            )
            logger.info("add_admin.success", extra=extra)
        except Exception as exc:
            logger.error("add_admin.error", extra={**extra, "error": str(exc)})
