from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, ReplyAttemptStatus, UserRole
from src.db.repositories import ReplyAttemptFilters, TicketReplyAttemptsRepository
from src.jobs.models import JobType
from src.libs.zendesk_client.models import Brand
from src.web_admin.dependencies import get_session_manager, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/replies", tags=["replies"])

DEFAULT_LIMIT = 50


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_status(value: str | None) -> ReplyAttemptStatus | None:
    if not value:
        return None
    try:
        return ReplyAttemptStatus(value)
    except ValueError:
        return None


def _parse_job_type(value: str | None) -> JobType | None:
    if not value:
        return None
    try:
        return JobType(value)
    except ValueError:
        return None


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    try:
        return Brand(int(value))
    except (TypeError, ValueError):
        return None


@router.get("")
async def get_replies(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ticket_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    brand: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Response:
    filters = ReplyAttemptFilters(
        ticket_id=_parse_int(ticket_id),
        status=_parse_status(status),
        job_type=_parse_job_type(job_type),
        brand=_parse_brand(brand),
    )

    async with session_local() as session:
        repo = TicketReplyAttemptsRepository(session)
        result = await repo.list_attempts(
            filters=filters,
            limit=limit,
            offset=offset,
        )

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "replies.html",
        {
            "active_page": "replies",
            "current_user": user,
            "csrf_token": csrf.raw,
            "result": result,
            "selected_ticket_id": ticket_id or "",
            "selected_status": status or "",
            "selected_job_type": job_type or "",
            "selected_brand": brand or "",
            "statuses": list(ReplyAttemptStatus),
            "job_types": [JobType.INITIAL_REPLY, JobType.FOLLOWUP_REPLY],
            "brands": list(Brand),
            "limit": result.limit,
            "offset": result.offset,
            "prev_offset": max(result.offset - result.limit, 0),
            "next_offset": result.offset + result.limit,
            "has_prev": result.offset > 0,
            "has_next": result.offset + result.limit < result.total,
            "flash": None,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response
