from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from src.db import session_local
from src.db.repositories import AdminUserNotFound, AdminUsersRepository
from src.web_admin.dependencies import get_session_manager
from src.web_admin.security import verify_password
from src.web_admin.session import SessionManager

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    return """
        <!doctype html>
        <html>
          <head><title>CS Admin Login</title></head>
          <body>
            <h1>CS Admin</h1>
            <form method="post" action="/admin/login">
              <label>Username <input name="username" autocomplete="username"></label><br>
              <label>Password <input name="password" type="password" autocomplete="current-password"></label><br>
              <button type="submit">Login</button>
            </form>
          </body>
        </html>
        """


@router.post("/login")
async def login(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
) -> Response:
    async with session_local() as session:
        async with session.begin():
            repo = AdminUsersRepository(session)

            try:
                user = await repo.get_by_username(username)
            except AdminUserNotFound:
                return HTMLResponse(
                    "Invalid username or password",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            if not user.is_active:
                return HTMLResponse(
                    "User is inactive",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            if not verify_password(password, user.password_hash):
                return HTMLResponse(
                    "Invalid username or password",
                    status_code=status.HTTP_401_UNAUTHORIZED,
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
) -> Response:
    response = RedirectResponse(
        url="/admin/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(
        key=session_manager.cookie_name,
        httponly=True,
        secure=session_manager.cookie_secure,
        samesite="lax",
    )
    return response
