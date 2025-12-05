from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TelegramUser as TelegramUserEntity, UserRole
from src.db.repositories.base import BaseRepository


class UserNotFound(Exception): ...


class TelegramUsersRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__("telegram_users_repository", session)

    async def create(
        self,
        telegram_id: int,
        username: str | None = None,
        role: UserRole = UserRole.USER,
    ) -> TelegramUserEntity:
        user = TelegramUserEntity(
            telegram_id=telegram_id,
            username=username,
            role=role,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_id(self, user_id: int) -> TelegramUserEntity:
        stmt = select(TelegramUserEntity).where(TelegramUserEntity.id == user_id)
        user = await self._session.scalar(stmt)
        if user is None:
            raise UserNotFound(f"Telegram user {user_id} does not exist.")
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUserEntity:
        stmt = select(TelegramUserEntity).where(TelegramUserEntity.telegram_id == telegram_id)
        user = await self._session.scalar(stmt)
        if user is None:
            raise UserNotFound(f"Telegram user {telegram_id} does not exist.")
        return user

    async def activate(self, user_id: int) -> None:
        stmt = (
            update(TelegramUserEntity)
            .where(TelegramUserEntity.id == user_id)
            .values(is_active=True)
        )
        await self._session.execute(stmt)

    async def deactivate(self, user_id: int) -> None:
        stmt = (
            update(TelegramUserEntity)
            .where(TelegramUserEntity.id == user_id)
            .values(is_active=False)
        )
        await self._session.execute(stmt)

    async def set_role(self, user_id: int, user_role: UserRole) -> None:
        stmt = (
            update(TelegramUserEntity)
            .where(TelegramUserEntity.id == user_id)
            .values(role=user_role)
        )
        await self._session.execute(stmt)

    async def list_users(self, include_inactive: bool = False) -> list[TelegramUserEntity]:
        stmt = select(TelegramUserEntity)
        if not include_inactive:
            stmt = stmt.where(TelegramUserEntity.is_active.is_(True))
        stmt = stmt.order_by(TelegramUserEntity.role.desc(), TelegramUserEntity.id.asc())
        result = await self._session.scalars(stmt)
        return list(result)
