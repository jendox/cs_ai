from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from src.db import session_local
from src.db.repositories import AdminUserNotFound, AdminUsersRepository
from src.web_admin.dependencies import get_session_manager, require_csrf
from src.web_admin.security import verify_password
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
) -> Response:
    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": csrf.raw, "error": None},
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


def render_login_error(
    request: Request,
    session_manager: SessionManager,
    *,
    error: str,
    status_code: int = status.HTTP_401_UNAUTHORIZED,
) -> Response:
    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"csrf_token": csrf.raw, "error": error},
        status_code=status_code,
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    async with session_local() as session:
        async with session.begin():
            repo = AdminUsersRepository(session)

            try:
                user = await repo.get_by_username(username)
            except AdminUserNotFound:
                return render_login_error(
                    request,
                    session_manager,
                    error="Invalid username or password",
                )

            if not user.is_active:
                return render_login_error(
                    request,
                    session_manager,
                    error="User is inactive",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            if not verify_password(password, user.password_hash):
                return render_login_error(
                    request,
                    session_manager,
                    error="Invalid username or password",
                )

            await repo.mark_login(user.id)

    response = RedirectResponse(
        url="/admin/zendesk/mode",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=session_manager.cookie_name,
        value=session_manager.create(user_id=user.id),
        max_age=session_manager.max_age_seconds,
        httponly=True,
        secure=session_manager.cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    response = RedirectResponse(
        url="/admin/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    session_manager.delete_session_cookies(response)
    return response
