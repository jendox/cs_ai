from typing import Any

from aiogram.filters import Filter
from aiogram.types import Message

from src.db.models import UserRole

PARTS_COUNT = 2


class RoleRequired(Filter):

    def __init__(self, required: UserRole | None = None):
        self.required = required

    async def __call__(self, message: Message, role: UserRole | None = None, **kwargs) -> bool:
        if self.required is None:
            return True
        return role.level >= self.required.level


class TicketId(Filter):
    def __init__(self, required: bool = True) -> None:
        self.required = required

    async def __call__(self, message: Message, **data: dict[str, Any]) -> bool | dict[str, Any]:
        if not message.text:
            return not self.required
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < PARTS_COUNT:
            if self.required:
                await message.answer("Нужно указать id тикета: <code>123456</code>")
                return False
            return True

        try:
            ticket_id = int(parts[1].strip())
        except ValueError:
            if self.required:
                await message.answer("ID тикета должен быть числом: <code>123456</code>")
                return False
            return True

        return {"ticket_id": ticket_id}
