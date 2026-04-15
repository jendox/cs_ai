from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories.base import BaseRepository


class AdminUserNotFound(Exception): ...


class AdminUsersRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="admin_users_repository", session=session)

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: UserRole = UserRole.USER,
        is_active: bool = True,
    ) -> AdminUserEntity:
        now = datetime_utils.utcnow()
        entity = AdminUserEntity(
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get_by_id(self, user_id: int) -> AdminUserEntity:
        entity = await self._session.get(AdminUserEntity, user_id)
        if entity is None:
            raise AdminUserNotFound(f"Admin user {user_id} not found.")
        return entity

    async def get_by_username(self, username: str) -> AdminUserEntity:
        stmt = select(AdminUserEntity).where(AdminUserEntity.username == username)
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise AdminUserNotFound(f"Admin user {username!r} not found.")
        return entity

    async def list_users(self, include_inactive: bool = False) -> list[AdminUserEntity]:
        stmt = select(AdminUserEntity).order_by(AdminUserEntity.id)
        if not include_inactive:
            stmt = stmt.where(AdminUserEntity.is_active.is_(True))
        return list(await self._session.scalars(stmt))

    async def set_password_hash(self, user_id: int, password_hash: str) -> None:
        entity = await self.get_by_id(user_id)
        entity.password_hash = password_hash
        entity.updated_at = datetime_utils.utcnow()
        await self._session.flush()

    async def set_role(self, user_id: int, role: UserRole) -> None:
        entity = await self.get_by_id(user_id)
        entity.role = role
        entity.updated_at = datetime_utils.utcnow()
        await self._session.flush()

    async def set_active(self, user_id: int, is_active: bool) -> None:
        entity = await self.get_by_id(user_id)
        entity.is_active = is_active
        entity.updated_at = datetime_utils.utcnow()
        await self._session.flush()

    async def mark_login(self, user_id: int) -> None:
        entity = await self.get_by_id(user_id)
        now = datetime_utils.utcnow()
        entity.last_login_at = now
        entity.updated_at = now
        await self._session.flush()
