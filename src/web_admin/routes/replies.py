from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from src import config
from src.brands import Brand
from src.db import session_local
from src.db.models import (
    AdminUser as AdminUserEntity,
    ReplyAttemptStatus,
    UserRole,
)
from src.db.repositories import (
    ReplyAttemptFilters,
    TicketReplyAttemptsRepository,
)
from src.jobs.models import JobType
from src.web_admin.dependencies import get_session_manager, require_role
from src.web_admin.pagination import DEFAULT_PAGE_LIMIT, PAGE_LIMIT_OPTIONS, parse_page_limit
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/replies", tags=["replies"])

DEFAULT_LIMIT = DEFAULT_PAGE_LIMIT
DEFAULT_PERIOD = "all"
PERIOD_OPTIONS: tuple[tuple[str, str], ...] = (
    ("24h", "Last 24h"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("all", "All time"),
)


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_ticket_id_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized if normalized.isdigit() else None


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
        return config.get_app_settings().brand.brand_for_id(int(value))
    except (TypeError, ValueError):
        return None


def _parse_period(value: str | None) -> str:
    allowed = {item[0] for item in PERIOD_OPTIONS}
    return value if value in allowed else DEFAULT_PERIOD


def _period_created_from(period: str) -> datetime | None:
    now = datetime.now(UTC)
    if period == "24h":
        return now - timedelta(hours=24)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    return None


def _replies_url(  # noqa: PLR0913
    *,
    ticket_id: str,
    status: str,
    job_type: str,
    brand: str,
    period: str,
    limit: int,
    offset: int,
) -> str:
    query = {
        "ticket_id": ticket_id,
        "status": status,
        "job_type": job_type,
        "brand": brand,
        "period": period,
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"/admin/replies?{urlencode(query)}"


@router.get("")
async def get_replies(  # noqa: PLR0913, PLR0914, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ticket_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    brand: str | None = None,
    period: str = DEFAULT_PERIOD,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Response:
    selected_limit = parse_page_limit(limit)
    selected_period = _parse_period(period)
    parsed_brand = _parse_brand(brand)
    settings = config.get_app_settings()
    filters = ReplyAttemptFilters(
        ticket_id=None,
        ticket_id_prefix=_parse_ticket_id_prefix(ticket_id),
        status=_parse_status(status),
        job_type=_parse_job_type(job_type),
        brand_id=settings.brand.id_for(parsed_brand) if parsed_brand else None,
        created_from=_period_created_from(selected_period),
    )

    async with session_local() as session:
        repo = TicketReplyAttemptsRepository(session)
        result = await repo.list_attempts(
            filters=filters,
            limit=selected_limit,
            offset=offset,
        )
        summary = await repo.get_summary(filters=filters)

    csrf = session_manager.prepare_csrf(request)
    selected_ticket_id = ticket_id or ""
    selected_status = status or ""
    selected_job_type = job_type or ""
    selected_brand = brand or ""
    prev_offset = max(result.offset - result.limit, 0)
    next_offset = result.offset + result.limit
    response = templates.TemplateResponse(
        request,
        "replies.html",
        {
            "active_page": "replies",
            "current_user": user,
            "csrf_token": csrf.raw,
            "result": result,
            "summary": summary,
            "selected_ticket_id": selected_ticket_id,
            "selected_status": selected_status,
            "selected_job_type": selected_job_type,
            "selected_brand": selected_brand,
            "selected_period": selected_period,
            "statuses": list(ReplyAttemptStatus),
            "job_types": [JobType.INITIAL_REPLY, JobType.FOLLOWUP_REPLY],
            "brands": list(Brand),
            "period_options": PERIOD_OPTIONS,
            "limit_options": PAGE_LIMIT_OPTIONS,
            "limit": result.limit,
            "offset": result.offset,
            "prev_url": _replies_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                job_type=selected_job_type,
                brand=selected_brand,
                period=selected_period,
                limit=result.limit,
                offset=prev_offset,
            ),
            "next_url": _replies_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                job_type=selected_job_type,
                brand=selected_brand,
                period=selected_period,
                limit=result.limit,
                offset=next_offset,
            ),
            "has_prev": result.offset > 0,
            "has_next": result.offset + result.limit < result.total,
            "flash": None,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.get("/tickets/{ticket_id}")
async def redirect_ticket_replies(ticket_id: int) -> Response:
    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/tickets/{ticket_id}/refresh")
async def redirect_ticket_refresh(
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
) -> Response:
    del user
    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
