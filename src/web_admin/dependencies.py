from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src import config
from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories import AdminUserNotFound, AdminUsersRepository
from src.web_admin.session import SessionManager


def get_session_manager() -> SessionManager:
    settings = config.app_settings.get()
    return SessionManager(settings.web)


async def get_current_admin_user(
    request: Request,
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
) -> AdminUserEntity:
    token = request.cookies.get(session_manager.cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    session_data = session_manager.load(token)
    if session_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    async with session_local() as session:
        repo = AdminUsersRepository(session)
        try:
            user = await repo.get_by_id(session_data.user_id)
        except AdminUserNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            ) from exc

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is inactive",
            )

        return user


def require_role(required: UserRole):
    async def dependency(
        user: Annotated[AdminUserEntity, Depends(get_current_admin_user)],
    ) -> AdminUserEntity:
        if user.role.level < required.level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency
