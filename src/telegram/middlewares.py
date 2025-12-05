from __future__ import annotations

import typing
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

if typing.TYPE_CHECKING:
    from src.telegram.admin import TelegramAdmin


class AuthenticationMiddleware(BaseMiddleware):

    def __init__(self, telegram_admin: TelegramAdmin) -> None:
        super().__init__()
        self._telegram_admin = telegram_admin

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user

        data["telegram_admin"] = self._telegram_admin

        if user is not None:
            data["role"] = await self._telegram_admin.get_user_role(user.id)
            data["telegram_id"] = user.id
        else:
            data["role"] = None

        return await handler(event, data)
