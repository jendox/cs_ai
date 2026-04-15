from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from src.admin.services import ZendeskAdminService
from src.db.models import AdminUser as AdminUserEntity, PostChannel, UserRole
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/zendesk", tags=["zendesk"])


@router.get("/mode")
async def get_zendesk_mode(
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    async with ZendeskAdminService() as service:
        channel = await service.get_mode()

    flash = None
    if saved is not None:
        flash = {
            "kind": "success",
            "message": "Zendesk mode updated.",
        }
    elif error == "invalid_mode":
        flash = {
            "kind": "error",
            "message": "Invalid Zendesk mode.",
        }

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "zendesk_mode.html",
        {
            "active_page": "zendesk",
            "current_user": user,
            "csrf_token": csrf.raw,
            "channel": channel,
            "flash": flash,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)

    return response


@router.post("/mode")
async def set_zendesk_mode(
    mode: Annotated[str, Form()],
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    if mode not in {"internal", "public"}:
        return RedirectResponse(
            url="/admin/zendesk/mode?error=invalid_mode",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    channel = PostChannel(mode)
    async with ZendeskAdminService() as service:
        await service.set_mode(channel, updated_by=user.username)

    return RedirectResponse(
        url="/admin/zendesk/mode?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
