from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError

from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories import AdminUserNotFound, AdminUsersRepository
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.security import hash_password
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/users", tags=["users"])

MIN_PASSWORD_LENGTH = 8

SAVED_MESSAGES: dict[str, str] = {
    "created": "User created.",
    "role": "User role updated.",
    "active": "User status updated.",
    "password": "Password updated.",
}
ERROR_MESSAGES: dict[str, str] = {
    "duplicate": "Username already exists.",
    "password": "Password must be at least 8 characters.",
    "password_mismatch": "Passwords do not match.",
    "username": "Username is required.",
    "role": "Invalid role.",
    "active": "Invalid user status.",
    "self": "You cannot lock yourself out.",
    "not_found": "User not found.",
}


def _build_flash(saved: str | None, error: str | None) -> dict[str, str] | None:
    if saved in SAVED_MESSAGES:
        return {"kind": "success", "message": SAVED_MESSAGES[saved]}
    if error in ERROR_MESSAGES:
        return {"kind": "error", "message": ERROR_MESSAGES[error]}
    return None


def _role_or_none(value: str | None) -> UserRole | None:
    if not value:
        return None
    try:
        role = UserRole(value)
    except ValueError:
        return None
    return role if role in UserRole.allowed_new_users() else None


def _active_bool(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _users_url(*, saved: str | None = None, error: str | None = None) -> str:
    if saved:
        return f"/admin/users?saved={saved}"
    if error:
        return f"/admin/users?error={error}"
    return "/admin/users"


@router.get("")
async def get_users(
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    async with session_local() as session:
        repo = AdminUsersRepository(session)
        users = await repo.list_users(include_inactive=True)

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "users.html",
        {
            "active_page": "users",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": _build_flash(saved, error),
            "users": users,
            "allowed_roles": UserRole.allowed_new_users(),
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("")
async def create_user(  # noqa: PLR0913, PLR0917
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    username: Annotated[str, Form()],
    role: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
) -> Response:
    del user

    username = username.strip()
    parsed_role = _role_or_none(role)
    if not username:
        return RedirectResponse(
            url=_users_url(error="username"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if parsed_role is None:
        return RedirectResponse(
            url=_users_url(error="role"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse(
            url=_users_url(error="password"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if password != password_confirm:
        return RedirectResponse(
            url=_users_url(error="password_mismatch"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        async with session_local() as session:
            async with session.begin():
                repo = AdminUsersRepository(session)
                await repo.create(
                    username=username,
                    password_hash=hash_password(password),
                    role=parsed_role,
                    is_active=True,
                )
    except IntegrityError:
        return RedirectResponse(
            url=_users_url(error="duplicate"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_users_url(saved="created"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{user_id}/role")
async def update_user_role(
    user_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    role: Annotated[str, Form()],
) -> Response:
    parsed_role = _role_or_none(role)
    if parsed_role is None:
        return RedirectResponse(
            url=_users_url(error="role"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if user.id == user_id and parsed_role != UserRole.SUPERADMIN:
        return RedirectResponse(
            url=_users_url(error="self"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        async with session_local() as session:
            async with session.begin():
                repo = AdminUsersRepository(session)
                await repo.set_role(user_id, parsed_role)
    except AdminUserNotFound:
        return RedirectResponse(
            url=_users_url(error="not_found"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_users_url(saved="role"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{user_id}/active")
async def update_user_active(
    user_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    is_active: Annotated[str, Form()],
) -> Response:
    parsed_active = _active_bool(is_active)
    if parsed_active is None:
        return RedirectResponse(
            url=_users_url(error="active"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if user.id == user_id and not parsed_active:
        return RedirectResponse(
            url=_users_url(error="self"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        async with session_local() as session:
            async with session.begin():
                repo = AdminUsersRepository(session)
                await repo.set_active(user_id, parsed_active)
    except AdminUserNotFound:
        return RedirectResponse(
            url=_users_url(error="not_found"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_users_url(saved="active"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{user_id}/password")
async def update_user_password(
    user_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
) -> Response:
    del user

    if len(password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse(
            url=_users_url(error="password"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if password != password_confirm:
        return RedirectResponse(
            url=_users_url(error="password_mismatch"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        async with session_local() as session:
            async with session.begin():
                repo = AdminUsersRepository(session)
                await repo.set_password_hash(user_id, hash_password(password))
    except AdminUserNotFound:
        return RedirectResponse(
            url=_users_url(error="not_found"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_users_url(saved="password"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
