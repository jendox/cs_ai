from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from src import config
from src.db import session_local
from src.db.models import (
    AdminUser as AdminUserEntity,
    Event as EventEntity,
    ReplyAttemptStatus,
    TicketReplyAttempt as TicketReplyAttemptEntity,
    UserRole,
)
from src.db.repositories import (
    EventsRepository,
    TicketFilters,
    TicketNotFound,
    TicketReplyAttemptsRepository,
    TicketsRepository,
)
from src.jobs.models import JobType
from src.libs.zendesk_client.client import ZendeskClientError, create_zendesk_client
from src.libs.zendesk_client.models import AGENT_IDS, Brand, Comment, TicketStatus
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates
from src.zendesk.models import comment_to_event

router = APIRouter(prefix="/tickets", tags=["tickets"])

DEFAULT_LIMIT = 50
OBSERVING_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    ("true", "Observed"),
    ("false", "Not observed"),
)
TICKET_SAVED_MESSAGES: dict[str, str] = {
    "refresh": "Zendesk comments refreshed.",
}
TICKET_ERROR_MESSAGES: dict[str, str] = {
    "refresh": "Zendesk comments could not be refreshed.",
    "ticket_not_found": "Ticket is not stored locally yet, so comments cannot be saved.",
}
EVENT_AUTHOR_LABELS: dict[str, str] = {
    "user": "customer",
    "agent": "agent",
    "system": "system",
    "unknown": "unknown author",
}
ATTEMPT_JOB_LABELS: dict[str, str] = {
    JobType.INITIAL_REPLY.value: "initial reply",
    JobType.FOLLOWUP_REPLY.value: "follow-up reply",
}
ATTEMPT_STATUS_TITLES: dict[ReplyAttemptStatus, str] = {
    ReplyAttemptStatus.GENERATED: "Bot Reply Generated",
    ReplyAttemptStatus.POSTED: "Bot Reply Posted",
    ReplyAttemptStatus.FAILED: "Bot Reply Failed",
    ReplyAttemptStatus.SKIPPED_DUPLICATE: "Duplicate Bot Reply Skipped",
    ReplyAttemptStatus.EMPTY_REPLY: "Empty Bot Reply",
}


@dataclass(frozen=True)
class TicketTimelineItem:
    kind: Literal["event", "comment", "attempt"]
    created_at: datetime
    title: str
    badge: str
    badge_class: str
    actor_class: str
    actor_label: str
    body: str | None = None
    error: str | None = None
    meta: tuple[str, ...] = ()


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


def _zendesk_ticket_url(ticket_id: int) -> str:
    settings = config.app_settings.get()
    subdomain = settings.zendesk.subdomain.strip()
    return f"https://{subdomain}.zendesk.com/agent/tickets/{ticket_id}"


def _last_successful_attempt(
    attempts: list[TicketReplyAttemptEntity],
) -> TicketReplyAttemptEntity | None:
    posted_attempts = [
        attempt
        for attempt in attempts
        if attempt.status == ReplyAttemptStatus.POSTED
    ]
    if not posted_attempts:
        return None
    return max(
        posted_attempts,
        key=lambda attempt: attempt.posted_at or attempt.created_at,
    )


def _comment_created_at(comment: Comment) -> datetime:
    return comment.created_at or datetime.min.replace(tzinfo=UTC)


def _event_created_at(event: EventEntity) -> datetime:
    return event.created_at or datetime.min.replace(tzinfo=UTC)


async def _load_zendesk_comments(ticket_id: int) -> tuple[list[Comment], str | None]:
    settings = config.app_settings.get()
    try:
        async with create_zendesk_client(settings.zendesk) as client:
            return await client.get_ticket_comments(ticket_id), None
    except (ZendeskClientError, httpx.HTTPError) as error:
        return [], str(error)


async def _store_zendesk_comments(ticket_id: int, comments: list[Comment]) -> int:
    inserted_count = 0
    async with session_local() as session:
        async with session.begin():
            events_repo = EventsRepository(session)
            for comment in comments:
                if comment.id is None or comment.created_at is None:
                    continue
                if await events_repo.insert_event(comment_to_event(ticket_id, comment)):
                    inserted_count += 1
    return inserted_count


async def _refresh_zendesk_comments(ticket_id: int) -> tuple[int, str | None]:
    comments, error = await _load_zendesk_comments(ticket_id)
    if error is not None:
        return 0, error
    return await _store_zendesk_comments(ticket_id, comments), None


def _ticket_flash(
    *,
    saved: str | None,
    error: str | None,
    count: int | None,
) -> dict[str, str] | None:
    if saved in TICKET_SAVED_MESSAGES:
        message = TICKET_SAVED_MESSAGES[saved]
        if count is not None:
            message = f"{message} Stored {count} new comments."
        return {"kind": "success", "message": message}
    if error in TICKET_ERROR_MESSAGES:
        return {"kind": "error", "message": TICKET_ERROR_MESSAGES[error]}
    return None


def _author_label(author_role: str | None) -> str | None:
    if author_role is None:
        return None
    return EVENT_AUTHOR_LABELS.get(author_role, author_role)


def _fallback_comment_author_label(comment: Comment) -> str:
    if comment.author_id in AGENT_IDS:
        return "agent"
    if comment.author_id is None:
        return "unknown author"
    return "customer"


def _event_title(event: EventEntity) -> str:
    if event.source_type != "comment":
        return "Zendesk Status Change"
    author = _author_label(event.author_role)
    if author == "customer":
        return "Customer Comment"
    if author == "agent":
        return "Agent Comment"
    return "Zendesk Comment"


def _event_badge(event: EventEntity) -> str:
    if event.kind == "comment_public":
        return "public"
    if event.kind == "comment_private":
        return "internal"
    if event.kind == "status_change":
        return "status"
    return event.kind


def _event_actor(event: EventEntity) -> tuple[str, str]:
    author = _author_label(event.author_role)
    if author == "customer":
        return "customer", "Customer"
    if author == "agent":
        return "agent", "Agent"
    if author == "system":
        return "system", "System"
    if event.source_type != "comment":
        return "system", "System"
    return "unknown", "Unknown"


def _build_event_timeline_item(event: EventEntity) -> TicketTimelineItem:
    is_comment = event.source_type == "comment"
    actor_class, actor_label = _event_actor(event)
    meta = [
        _author_label(event.author_role),
        f"Author ID {event.author_id}" if event.author_id else None,
        f"Zendesk comment #{event.source_id}" if is_comment and event.source_id else None,
    ]
    return TicketTimelineItem(
        kind="event",
        created_at=_event_created_at(event),
        title=_event_title(event),
        badge=_event_badge(event),
        badge_class=event.kind,
        actor_class=actor_class,
        actor_label=actor_label,
        body=event.body,
        meta=tuple(item for item in meta if item),
    )


def _build_comment_timeline_item(comment: Comment) -> TicketTimelineItem:
    is_public = bool(comment.public)
    author = _fallback_comment_author_label(comment)
    meta = [
        author,
        f"Author ID {comment.author_id}" if comment.author_id else None,
        f"Zendesk comment #{comment.id}" if comment.id else None,
    ]
    return TicketTimelineItem(
        kind="comment",
        created_at=_comment_created_at(comment),
        title="Agent Comment" if author == "agent" else "Customer Comment",
        badge="public" if is_public else "internal",
        badge_class="public" if is_public else "internal",
        actor_class="agent" if author == "agent" else "customer",
        actor_label="Agent" if author == "agent" else "Customer",
        body=comment.body or "-",
        meta=tuple(item for item in meta if item),
    )


def _build_attempt_timeline_item(attempt: TicketReplyAttemptEntity) -> TicketTimelineItem:
    meta = [
        ATTEMPT_JOB_LABELS.get(attempt.job_type, attempt.job_type),
        attempt.channel.value,
        f"Zendesk comment #{attempt.zendesk_comment_id}" if attempt.zendesk_comment_id else None,
        attempt.model,
        attempt.provider,
    ]
    return TicketTimelineItem(
        kind="attempt",
        created_at=attempt.created_at,
        title=ATTEMPT_STATUS_TITLES.get(attempt.status, "Bot Reply Attempt"),
        badge=attempt.status.value,
        badge_class=attempt.status.value,
        actor_class="bot",
        actor_label="Bot",
        body=attempt.body,
        error=attempt.error,
        meta=tuple(item for item in meta if item),
    )


def _build_ticket_timeline(
    *,
    events: list[EventEntity],
    fallback_comments: list[Comment],
    attempts: list[TicketReplyAttemptEntity],
) -> list[TicketTimelineItem]:
    items = [
        *(_build_event_timeline_item(event) for event in events),
        *(_build_comment_timeline_item(comment) for comment in fallback_comments),
        *(_build_attempt_timeline_item(attempt) for attempt in attempts),
    ]
    return sorted(items, key=lambda item: item.created_at)


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


@router.get("/{ticket_id}")
async def get_ticket_detail(  # noqa: PLR0913, PLR0917
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    saved: str | None = None,
    error: str | None = None,
    count: int | None = None,
) -> Response:
    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        events_repo = EventsRepository(session)
        attempts_repo = TicketReplyAttemptsRepository(session)

        try:
            ticket = await tickets_repo.get_ticket_by_id(ticket_id)
        except TicketNotFound:
            ticket = None

        events = await events_repo.list_by_ticket(ticket_id)
        comment_events = [event for event in events if event.source_type == "comment"]
        attempts = await attempts_repo.list_by_ticket(ticket_id)

    fallback_comments: list[Comment] = []
    comments_error = None
    comments_source = "local"
    if not comment_events:
        fallback_comments, comments_error = await _load_zendesk_comments(ticket_id)
        comments_source = "zendesk_fallback"

    timeline_items = _build_ticket_timeline(
        events=events,
        fallback_comments=fallback_comments,
        attempts=attempts,
    )
    last_successful_attempt = _last_successful_attempt(attempts)

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "ticket_detail.html",
        {
            "active_page": "tickets",
            "current_user": user,
            "csrf_token": csrf.raw,
            "ticket_id": ticket_id,
            "ticket": ticket,
            "events": events,
            "attempts": attempts,
            "comments": comment_events or fallback_comments,
            "comments_source": comments_source,
            "timeline_items": timeline_items,
            "comments_error": comments_error,
            "last_successful_attempt": last_successful_attempt,
            "zendesk_ticket_url": _zendesk_ticket_url(ticket_id),
            "flash": _ticket_flash(saved=saved, error=error, count=count),
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("/{ticket_id}/refresh")
async def refresh_ticket_comments(
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    del user

    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        try:
            await tickets_repo.get_ticket_by_id(ticket_id)
        except TicketNotFound:
            return RedirectResponse(
                url=f"/admin/tickets/{ticket_id}?error=ticket_not_found",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    count, refresh_error = await _refresh_zendesk_comments(ticket_id)
    if refresh_error is not None:
        return RedirectResponse(
            url=f"/admin/tickets/{ticket_id}?error=refresh",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}?saved=refresh&count={count}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
