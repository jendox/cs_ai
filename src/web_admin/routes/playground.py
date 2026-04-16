from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from src.admin.services.llm_playground import LLMPlaygroundService
from src.ai.context import LLMContext
from src.db import session_local
from src.db.models import (
    AdminUser as AdminUserEntity,
    LLMPlaygroundMessageRole,
    LLMPlaygroundTicketStatus,
    UserRole,
)
from src.db.repositories import (
    LLMPlaygroundFilters,
    LLMPlaygroundMessageCreate,
    LLMPlaygroundRepository,
    LLMPlaygroundTicketCreate,
    LLMPlaygroundTicketNotFound,
)
from src.libs.zendesk_client.models import Brand
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/playground", tags=["playground"])

DEFAULT_LIMIT = 50

SAVED_MESSAGES: dict[str, str] = {
    "created": "Playground ticket created.",
    "message": "Customer reply added.",
    "initial": "Initial reply generated.",
    "followup": "Follow-up reply generated.",
    "closed": "Playground ticket closed.",
}
ERROR_MESSAGES: dict[str, str] = {
    "not_found": "Playground ticket was not found.",
    "closed": "Playground ticket is closed.",
    "empty": "Message cannot be empty.",
    "generation": "Reply could not be generated.",
}


def _parse_ticket_id_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized if normalized.isdigit() else None


def _parse_status(value: str | None) -> LLMPlaygroundTicketStatus | None:
    if not value:
        return None
    try:
        return LLMPlaygroundTicketStatus(value)
    except ValueError:
        return None


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    try:
        return Brand(int(value))
    except (TypeError, ValueError):
        return None


def _playground_url(
    *,
    ticket_id: str,
    status_: str,
    brand: str,
    limit: int,
    offset: int,
) -> str:
    query = {
        "ticket_id": ticket_id,
        "status": status_,
        "brand": brand,
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"/admin/playground?{urlencode(query)}"


def _flash(saved: str | None, error: str | None) -> dict[str, str] | None:
    if saved in SAVED_MESSAGES:
        return {"kind": "success", "message": SAVED_MESSAGES[saved]}
    if error in ERROR_MESSAGES:
        return {"kind": "error", "message": ERROR_MESSAGES[error]}
    return None


def _llm_context(request: Request) -> LLMContext:
    return request.app.state.llm_context


@router.get("")
async def get_playground(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ticket_id: str | None = None,
    status: str | None = None,
    brand: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    filters = LLMPlaygroundFilters(
        ticket_id_prefix=_parse_ticket_id_prefix(ticket_id),
        status=_parse_status(status),
        brand=_parse_brand(brand),
    )

    async with session_local() as session:
        repo = LLMPlaygroundRepository(session)
        result = await repo.list_tickets(
            filters=filters,
            limit=limit,
            offset=offset,
        )

    csrf = session_manager.create_csrf_token()
    selected_ticket_id = ticket_id or ""
    selected_status = status or ""
    selected_brand = brand or ""
    prev_offset = max(result.offset - result.limit, 0)
    next_offset = result.offset + result.limit
    response = templates.TemplateResponse(
        request,
        "playground.html",
        {
            "active_page": "playground",
            "current_user": user,
            "csrf_token": csrf.raw,
            "result": result,
            "selected_ticket_id": selected_ticket_id,
            "selected_status": selected_status,
            "selected_brand": selected_brand,
            "statuses": list(LLMPlaygroundTicketStatus),
            "brands": list(Brand),
            "limit": result.limit,
            "offset": result.offset,
            "prev_url": _playground_url(
                ticket_id=selected_ticket_id,
                status_=selected_status,
                brand=selected_brand,
                limit=result.limit,
                offset=prev_offset,
            ),
            "next_url": _playground_url(
                ticket_id=selected_ticket_id,
                status_=selected_status,
                brand=selected_brand,
                limit=result.limit,
                offset=next_offset,
            ),
            "has_prev": result.offset > 0,
            "has_next": result.offset + result.limit < result.total,
            "flash": _flash(saved, error),
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("/tickets")
async def create_playground_ticket(
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    brand: Annotated[str, Form()],
    subject: Annotated[str, Form()],
    body: Annotated[str, Form()],
) -> Response:
    parsed_brand = _parse_brand(brand)
    normalized_subject = subject.strip()
    normalized_body = body.strip()
    if parsed_brand is None or not normalized_subject or not normalized_body:
        return RedirectResponse(
            url="/admin/playground?error=empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with session_local() as session:
        async with session.begin():
            repo = LLMPlaygroundRepository(session)
            ticket = await repo.create_ticket(
                LLMPlaygroundTicketCreate(
                    brand=parsed_brand,
                    subject=normalized_subject,
                    body=normalized_body,
                    created_by=user.username,
                ),
            )

    return RedirectResponse(
        url=f"/admin/playground/tickets/{ticket.id}?saved=created",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/tickets/{ticket_id}")
async def get_playground_ticket(
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    async with session_local() as session:
        repo = LLMPlaygroundRepository(session)
        try:
            ticket = await repo.get_ticket(ticket_id)
        except LLMPlaygroundTicketNotFound:
            return RedirectResponse(
                url="/admin/playground?error=not_found",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        messages = await repo.list_messages(ticket_id)
        runs = await repo.list_runs(ticket_id)

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "playground_ticket.html",
        {
            "active_page": "playground",
            "current_user": user,
            "csrf_token": csrf.raw,
            "ticket": ticket,
            "messages": messages,
            "runs": runs,
            "brands": list(Brand),
            "flash": _flash(saved, error),
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("/tickets/{ticket_id}/messages")
async def add_customer_message(
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    body: Annotated[str, Form()],
) -> Response:
    del user
    normalized_body = body.strip()
    if not normalized_body:
        return RedirectResponse(
            url=f"/admin/playground/tickets/{ticket_id}?error=empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with session_local() as session:
        async with session.begin():
            repo = LLMPlaygroundRepository(session)
            try:
                ticket = await repo.get_ticket(ticket_id)
            except LLMPlaygroundTicketNotFound:
                return RedirectResponse(
                    url="/admin/playground?error=not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            if ticket.status == LLMPlaygroundTicketStatus.CLOSED:
                return RedirectResponse(
                    url=f"/admin/playground/tickets/{ticket_id}?error=closed",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            await repo.add_message(
                LLMPlaygroundMessageCreate(
                    ticket_id=ticket_id,
                    role=LLMPlaygroundMessageRole.USER,
                    body=normalized_body,
                ),
            )

    return RedirectResponse(
        url=f"/admin/playground/tickets/{ticket_id}?saved=message",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _generate_reply(
    *,
    request: Request,
    ticket_id: int,
    user: AdminUserEntity,
    generation_type: str,
) -> Response:
    async with session_local() as session:
        async with session.begin():
            repo = LLMPlaygroundRepository(session)
            try:
                ticket = await repo.get_ticket(ticket_id)
            except LLMPlaygroundTicketNotFound:
                return RedirectResponse(
                    url="/admin/playground?error=not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            if ticket.status == LLMPlaygroundTicketStatus.CLOSED:
                return RedirectResponse(
                    url=f"/admin/playground/tickets/{ticket_id}?error=closed",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            messages = await repo.list_messages(ticket_id)
            service = LLMPlaygroundService(
                session=session,
                llm_context=_llm_context(request),
            )
            if generation_type == "initial":
                result = await service.generate_initial_reply(
                    ticket=ticket,
                    messages=messages,
                    created_by=user.username,
                )
            else:
                result = await service.generate_followup_reply(
                    ticket=ticket,
                    messages=messages,
                    created_by=user.username,
                )

    saved = generation_type if result.reply else None
    error = None if result.reply else "generation"
    query = urlencode({"saved": saved or "", "error": error or ""})
    return RedirectResponse(
        url=f"/admin/playground/tickets/{ticket_id}?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/tickets/{ticket_id}/generate-initial")
async def generate_initial_reply(
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    return await _generate_reply(
        request=request,
        ticket_id=ticket_id,
        user=user,
        generation_type="initial",
    )


@router.post("/tickets/{ticket_id}/generate-followup")
async def generate_followup_reply(
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    return await _generate_reply(
        request=request,
        ticket_id=ticket_id,
        user=user,
        generation_type="followup",
    )


@router.post("/tickets/{ticket_id}/close")
async def close_playground_ticket(
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    del user
    async with session_local() as session:
        async with session.begin():
            repo = LLMPlaygroundRepository(session)
            try:
                await repo.close_ticket(ticket_id)
            except LLMPlaygroundTicketNotFound:
                return RedirectResponse(
                    url="/admin/playground?error=not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

    return RedirectResponse(
        url=f"/admin/playground/tickets/{ticket_id}?saved=closed",
        status_code=status.HTTP_303_SEE_OTHER,
    )
