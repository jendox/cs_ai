from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories import TicketFilters, TicketsRepository
from src.libs.zendesk_client.models import Brand, TicketStatus
from src.web_admin.dependencies import get_session_manager, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/tickets", tags=["tickets"])

DEFAULT_LIMIT = 50
OBSERVING_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    ("true", "Observed"),
    ("false", "Not observed"),
)


def _parse_ticket_id_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized if normalized.isdigit() else None


def _parse_status(value: str | None) -> TicketStatus | None:
    if not value:
        return None
    try:
        return TicketStatus(value)
    except ValueError:
        return None


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    try:
        return Brand(int(value))
    except (TypeError, ValueError):
        return None


def _parse_observing(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _tickets_url(
    *,
    ticket_id: str,
    status: str,
    brand: str,
    observing: str,
    limit: int,
    offset: int,
) -> str:
    query = {
        "ticket_id": ticket_id,
        "status": status,
        "brand": brand,
        "observing": observing,
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"/admin/tickets?{urlencode(query)}"


@router.get("")
async def get_tickets(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ticket_id: str | None = None,
    status: str | None = None,
    brand: str | None = None,
    observing: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Response:
    filters = TicketFilters(
        ticket_id_prefix=_parse_ticket_id_prefix(ticket_id),
        status=_parse_status(status),
        brand=_parse_brand(brand),
        observing=_parse_observing(observing),
    )

    async with session_local() as session:
        repo = TicketsRepository(session)
        result = await repo.list_tickets(
            filters=filters,
            limit=limit,
            offset=offset,
        )

    csrf = session_manager.create_csrf_token()
    selected_ticket_id = ticket_id or ""
    selected_status = status or ""
    selected_brand = brand or ""
    selected_observing = observing or ""
    prev_offset = max(result.offset - result.limit, 0)
    next_offset = result.offset + result.limit
    response = templates.TemplateResponse(
        request,
        "tickets.html",
        {
            "active_page": "tickets",
            "current_user": user,
            "csrf_token": csrf.raw,
            "result": result,
            "selected_ticket_id": selected_ticket_id,
            "selected_status": selected_status,
            "selected_brand": selected_brand,
            "selected_observing": selected_observing,
            "statuses": list(TicketStatus),
            "brands": list(Brand),
            "observing_options": OBSERVING_OPTIONS,
            "limit": result.limit,
            "offset": result.offset,
            "prev_url": _tickets_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                brand=selected_brand,
                observing=selected_observing,
                limit=result.limit,
                offset=prev_offset,
            ),
            "next_url": _tickets_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                brand=selected_brand,
                observing=selected_observing,
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
