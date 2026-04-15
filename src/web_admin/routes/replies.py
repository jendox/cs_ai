from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Request, Response

from src import config
from src.db import session_local
from src.db.models import (
    AdminUser as AdminUserEntity,
    ReplyAttemptStatus,
    TicketReplyAttempt as TicketReplyAttemptEntity,
    UserRole,
)
from src.db.repositories import ReplyAttemptFilters, TicketNotFound, TicketReplyAttemptsRepository, TicketsRepository
from src.jobs.models import JobType
from src.libs.zendesk_client.client import ZendeskClientError, create_zendesk_client
from src.libs.zendesk_client.models import Brand, Comment
from src.web_admin.dependencies import get_session_manager, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/replies", tags=["replies"])

DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class TicketTimelineItem:
    kind: Literal["comment", "attempt"]
    created_at: datetime
    title: str
    badge: str
    badge_class: str
    body: str | None = None
    error: str | None = None
    meta: tuple[str, ...] = ()


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


def _comment_created_at(comment: Comment) -> datetime:
    return comment.created_at or datetime.min.replace(tzinfo=UTC)


async def _load_zendesk_comments(ticket_id: int) -> tuple[list[Comment], str | None]:
    settings = config.app_settings.get()
    try:
        async with create_zendesk_client(settings.zendesk) as client:
            return await client.get_ticket_comments(ticket_id), None
    except (ZendeskClientError, httpx.HTTPError) as error:
        return [], str(error)


def _build_comment_timeline_item(comment: Comment) -> TicketTimelineItem:
    is_public = bool(comment.public)
    meta = [
        f"Author {comment.author_id}" if comment.author_id else None,
        f"#{comment.id}" if comment.id else None,
    ]
    return TicketTimelineItem(
        kind="comment",
        created_at=_comment_created_at(comment),
        title="Zendesk Comment",
        badge="public" if is_public else "internal",
        badge_class="public" if is_public else "internal",
        body=comment.body or "-",
        meta=tuple(item for item in meta if item),
    )


def _build_attempt_timeline_item(attempt: TicketReplyAttemptEntity) -> TicketTimelineItem:
    meta = [
        attempt.job_type,
        attempt.channel.value,
        f"Comment {attempt.zendesk_comment_id}" if attempt.zendesk_comment_id else None,
        attempt.model,
        attempt.provider,
    ]
    return TicketTimelineItem(
        kind="attempt",
        created_at=attempt.created_at,
        title="Reply Attempt",
        badge=attempt.status.value,
        badge_class=attempt.status.value,
        body=attempt.body,
        error=attempt.error,
        meta=tuple(item for item in meta if item),
    )


def _build_ticket_timeline(
    *,
    comments: list[Comment],
    attempts: list[TicketReplyAttemptEntity],
) -> list[TicketTimelineItem]:
    items = [
        *(_build_comment_timeline_item(comment) for comment in comments),
        *(_build_attempt_timeline_item(attempt) for attempt in attempts),
    ]
    return sorted(items, key=lambda item: item.created_at)


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


@router.get("/tickets/{ticket_id}")
async def get_ticket_replies(
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
) -> Response:
    async with session_local() as session:
        tickets_repo = TicketsRepository(session)
        attempts_repo = TicketReplyAttemptsRepository(session)

        try:
            ticket = await tickets_repo.get_ticket_by_id(ticket_id)
        except TicketNotFound:
            ticket = None

        attempts = await attempts_repo.list_by_ticket(ticket_id)

    comments, comments_error = await _load_zendesk_comments(ticket_id)
    timeline_items = _build_ticket_timeline(comments=comments, attempts=attempts)

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "reply_ticket.html",
        {
            "active_page": "replies",
            "current_user": user,
            "csrf_token": csrf.raw,
            "ticket_id": ticket_id,
            "ticket": ticket,
            "attempts": attempts,
            "comments": comments,
            "timeline_items": timeline_items,
            "comments_error": comments_error,
            "flash": None,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response
