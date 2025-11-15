import uuid
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.config import TelegramSettings
from src.db import session_local
from src.db.models import UserRole
from src.db.repositories.telegram import TelegramUsersRepository, UserNotFound
from src.libs.zendesk_client.models import Brand
from src.logs.filters import log_ctx
from src.services import Service
from src.telegram.handlers import routers
from src.telegram.middlewares import AuthenticationMiddleware


@asynccontextmanager
async def log_context(user_id: int, brand: Brand):
    token = log_ctx.set({
        "brand": brand.value,
        "user_id": user_id,
        "iteration_id": uuid.uuid4().hex[:8],
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass


class TelegramAdmin(Service):
    def __init__(self, settings: TelegramSettings, brand: Brand) -> None:
        super().__init__(name="telegram_admin", brand=brand)
        self._settings = settings
        self._user_roles: dict[int, UserRole] = {}

    async def get_user_role(self, telegram_id: int) -> UserRole:
        if telegram_id == self._settings.chat_id:
            return UserRole.SUPERADMIN

        role = self._user_roles.get(telegram_id)
        if role is not None:
            return role

        async with session_local() as session:
            users_repo = TelegramUsersRepository(session)
            try:
                user = await users_repo.get_by_telegram_id(telegram_id)
                role = UserRole(user.role)
            except UserNotFound:
                role = UserRole.ANONYMOUS

        self._user_roles[telegram_id] = role
        return role

    def invalidate_cache_role(self, telegram_id: int) -> None:
        self._user_roles.pop(telegram_id, None)

    async def _ensure_superadmin(self) -> None:
        user_id = self._settings.chat_id
        self._user_roles[user_id] = UserRole.SUPERADMIN
        async with session_local() as session:
            repo = TelegramUsersRepository(session)
            async with session.begin():
                try:
                    await repo.get_by_telegram_id(user_id)
                except UserNotFound:
                    await repo.create(user_id, self._settings.username, UserRole.SUPERADMIN)

    async def run(self) -> None:
        await self._ensure_superadmin()

        dp = Dispatcher()
        dp.message.outer_middleware(AuthenticationMiddleware(self))
        dp.callback_query.outer_middleware(AuthenticationMiddleware(self))

        dp.include_routers(*routers)

        bot = Bot(
            token=self._settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
        await dp.start_polling(bot)
