import logging

from sqlalchemy.exc import IntegrityError

from src.config import WebAdminSettings
from src.db import session_local
from src.db.models import UserRole
from src.db.repositories import AdminUserNotFound, AdminUsersRepository
from src.web_admin.security import hash_password

logger = logging.getLogger("web_admin.bootstrap")


async def bootstrap_superadmin(settings: WebAdminSettings) -> None:
    async with session_local() as session:
        repo = AdminUsersRepository(session)

        async with session.begin():
            try:
                user = await repo.get_by_username(settings.bootstrap_username)
            except AdminUserNotFound:
                try:
                    await repo.create(
                        username=settings.bootstrap_username,
                        password_hash=hash_password(settings.bootstrap_password.get_secret_value()),
                        role=UserRole.SUPERADMIN,
                        is_active=True,
                    )
                except IntegrityError:
                    logger.info("superadmin.bootstrap.concurrent_skip")
                    return

                logger.info(
                    "superadmin.bootstrap.created",
                    extra={"username": settings.bootstrap_username},
                )
                return

            if user.role != UserRole.SUPERADMIN or not user.is_active:
                user.role = UserRole.SUPERADMIN
                user.is_active = True
                logger.info(
                    "superadmin.bootstrap.updated",
                    extra={"username": settings.bootstrap_username},
                )
