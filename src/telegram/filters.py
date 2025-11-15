from aiogram.filters import Filter
from aiogram.types import Message

from src.db.models import UserRole


class RoleFilter(Filter):

    def __init__(self, *roles: UserRole):
        self._roles = roles

    async def __call__(self, message: Message, role: UserRole) -> bool:
        return role in self._roles
