from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from src import config
from src.brands import Brand
from src.db import session_local
from src.db.models import (
    AdminUser as AdminUserEntity,
    Event as EventEntity,
    ReplyAttemptStatus,
    TicketCommentAttachment as TicketCommentAttachmentEntity,
    TicketReplyAttempt as TicketReplyAttemptEntity,
    UserRole,
)
from src.db.repositories import (
    CLASSIFICATION_DECISION_CUSTOMER,
    CLASSIFICATION_DECISION_SERVICE,
    CLASSIFICATION_DECISION_UNKNOWN,
    CLASSIFICATION_SOURCE_LLM,
    CLASSIFICATION_SOURCE_MANUAL,
    CLASSIFICATION_SOURCE_RULE,
    CheckpointsRepository,
    EventsRepository,
    TicketClassificationAuditCreate,
    TicketClassificationAuditsRepository,
    TicketCommentAttachmentsRepository,
    TicketFilters,
    TicketNotFound,
    TicketReplyAttemptsRepository,
    TicketsFilterRuleRepository,
    TicketsRepository,
)
from src.jobs.models import JobType
from src.libs.zendesk_client.client import ZendeskClientError, create_zendesk_client
from src.libs.zendesk_client.models import AGENT_IDS, Attachment, Comment, Ticket, TicketStatus
from src.services.ticket_attachments import store_ticket_comment_attachments
from src.services.ticket_classification import TicketClassificationService
from src.tickets_filter.cache import get_checkpoint_name, tickets_filter_cache
from src.tickets_filter.config import TicketsFilterRuleKind
from src.tickets_filter.dto import TicketsFilterRuleDTO
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.pagination import DEFAULT_PAGE_LIMIT, PAGE_LIMIT_OPTIONS, parse_page_limit
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates
from src.zendesk.models import comment_to_event

router = APIRouter(prefix="/tickets", tags=["tickets"])

DEFAULT_LIMIT = DEFAULT_PAGE_LIMIT
MAX_RULE_VALUE_LENGTH = 128
BYTE_UNIT = 1024
OBSERVING_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    ("true", "Observed"),
    ("false", "Not observed"),
)
CLASSIFICATION_DECISION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    (CLASSIFICATION_DECISION_CUSTOMER, "Customer"),
    (CLASSIFICATION_DECISION_SERVICE, "Service"),
    (CLASSIFICATION_DECISION_UNKNOWN, "Unknown"),
)
CLASSIFICATION_SOURCE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    (CLASSIFICATION_SOURCE_RULE, "Rule"),
    (CLASSIFICATION_SOURCE_LLM, "LLM"),
    (CLASSIFICATION_SOURCE_MANUAL, "Manual"),
)
MANUAL_CLASSIFICATION_DECISION_OPTIONS: tuple[tuple[str, str], ...] = (
    (CLASSIFICATION_DECISION_CUSTOMER, "Customer"),
    (CLASSIFICATION_DECISION_SERVICE, "Service"),
    (CLASSIFICATION_DECISION_UNKNOWN, "Unknown"),
)
TICKET_SAVED_MESSAGES: dict[str, str] = {
    "refresh": "Zendesk comments refreshed.",
    "classification": "Ticket classification updated.",
    "auto_classification": "Ticket reclassified.",
    "filter_rule": "Filter rule created.",
}
TICKET_ERROR_MESSAGES: dict[str, str] = {
    "refresh": "Zendesk comments could not be refreshed.",
    "ticket_not_found": "Ticket is not stored locally yet, so comments cannot be saved.",
    "zendesk_ticket": "Zendesk ticket could not be loaded.",
    "classification_decision": "Invalid classification decision.",
    "filter_rule_kind": "Invalid filter rule kind.",
    "filter_rule_value": "Rule value is required and must be 128 characters or fewer.",
    "filter_rule_regex": "Invalid regular expression.",
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
class TimelineAttachment:
    file_name: str
    content_type: str | None
    size_label: str | None
    content_url: str | None
    thumbnail_url: str | None


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
    attachments: tuple[TimelineAttachment, ...] = ()


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
        return config.get_app_settings().brand.brand_for_id(int(value))
    except (TypeError, ValueError):
        return None


def _parse_observing(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _parse_classification_decision(value: str | None) -> str | None:
    if value in {CLASSIFICATION_DECISION_CUSTOMER, CLASSIFICATION_DECISION_SERVICE, CLASSIFICATION_DECISION_UNKNOWN}:
        return value
    return None


def _parse_classification_source(value: str | None) -> str | None:
    if value in {
        CLASSIFICATION_SOURCE_RULE,
        CLASSIFICATION_SOURCE_LLM,
        CLASSIFICATION_SOURCE_MANUAL,
    }:
        return value
    return None


def _parse_filter_rule_kind(value: str | None) -> TicketsFilterRuleKind | None:
    if not value:
        return None
    try:
        return TicketsFilterRuleKind(value)
    except ValueError:
        return None


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_via_channel(value: str | None) -> str | None:
    normalized = _normalize_optional(value)
    return normalized.lower() if normalized is not None else None


def _validate_filter_rule_value(value: str, *, is_regex: bool) -> str | None:
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_RULE_VALUE_LENGTH:
        return "filter_rule_value"
    if is_regex:
        try:
            re.compile(normalized)
        except re.error:
            return "filter_rule_regex"
    return None


def _manual_classification_detail(username: str, note: str | None) -> str:
    normalized_note = (note or "").strip()
    if normalized_note:
        return f"updated by {username}: {normalized_note}"
    return f"updated by {username}"


def _tickets_url(  # noqa: PLR0913
    *,
    ticket_id: str,
    status: str,
    brand: str,
    observing: str,
    classification_decision: str,
    classification_source: str,
    limit: int,
    offset: int,
) -> str:
    query = {
        "ticket_id": ticket_id,
        "status": status,
        "brand": brand,
        "observing": observing,
        "classification_decision": classification_decision,
        "classification_source": classification_source,
        "limit": str(limit),
        "offset": str(offset),
    }
    return f"/admin/tickets?{urlencode(query)}"


async def _touch_filter_checkpoints(session) -> None:
    from src import datetime_utils

    settings = config.get_app_settings()
    now = datetime_utils.utcnow()
    checkpoints_repo = CheckpointsRepository(session)
    for brand in settings.brand.supported:
        await checkpoints_repo.set_checkpoint(get_checkpoint_name(settings.brand.id_for(brand)), now)
    tickets_filter_cache.clear()


def _zendesk_ticket_url(ticket_id: int) -> str:
    settings = config.get_app_settings()
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
    settings = config.get_app_settings()
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
            await store_ticket_comment_attachments(session, ticket_id=ticket_id, comments=comments)
    return inserted_count


async def _refresh_zendesk_comments(ticket_id: int) -> tuple[int, str | None]:
    comments, error = await _load_zendesk_comments(ticket_id)
    if error is not None:
        return 0, error
    return await _store_zendesk_comments(ticket_id, comments), None


async def _load_zendesk_ticket(ticket_id: int) -> tuple[Ticket | None, str | None]:
    settings = config.get_app_settings()
    try:
        async with create_zendesk_client(settings.zendesk) as client:
            return await client.get_ticket(ticket_id), None
    except (ZendeskClientError, httpx.HTTPError) as error:
        return None, str(error)


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


def _format_attachment_size(size: int | None) -> str | None:
    if size is None:
        return None
    if size < BYTE_UNIT:
        return f"{size} B"
    if size < BYTE_UNIT * BYTE_UNIT:
        return f"{size / BYTE_UNIT:.1f} KB"
    return f"{size / (BYTE_UNIT * BYTE_UNIT):.1f} MB"


def _attachment_url(attachment: Attachment) -> str | None:
    return attachment.mapped_content_url or attachment.content_url


def _timeline_attachment(attachment: Attachment) -> TimelineAttachment:
    thumbnail = attachment.thumbnails[0] if attachment.thumbnails else None
    return TimelineAttachment(
        file_name=attachment.file_name or f"attachment-{attachment.id or 'unknown'}",
        content_type=attachment.content_type,
        size_label=_format_attachment_size(attachment.size),
        content_url=_attachment_url(attachment),
        thumbnail_url=_attachment_url(thumbnail) if thumbnail is not None else None,
    )


def _stored_timeline_attachment(attachment: TicketCommentAttachmentEntity) -> TimelineAttachment:
    return TimelineAttachment(
        file_name=attachment.file_name,
        content_type=attachment.content_type,
        size_label=_format_attachment_size(attachment.size),
        content_url=attachment.mapped_content_url or attachment.content_url,
        thumbnail_url=attachment.thumbnail_url,
    )


def _comment_attachments(comments: list[Comment]) -> dict[str, tuple[TimelineAttachment, ...]]:
    result: dict[str, tuple[TimelineAttachment, ...]] = {}
    for comment in comments:
        if comment.id is None or not comment.attachments:
            continue
        result[str(comment.id)] = tuple(_timeline_attachment(item) for item in comment.attachments)
    return result


def _stored_comment_attachments(
    attachments: list[TicketCommentAttachmentEntity],
) -> dict[str, tuple[TimelineAttachment, ...]]:
    grouped: dict[str, list[TimelineAttachment]] = {}
    for attachment in attachments:
        grouped.setdefault(attachment.comment_id, []).append(_stored_timeline_attachment(attachment))
    return {comment_id: tuple(items) for comment_id, items in grouped.items()}


def _merge_comment_attachments(
    stored: dict[str, tuple[TimelineAttachment, ...]],
    live: dict[str, tuple[TimelineAttachment, ...]],
) -> dict[str, tuple[TimelineAttachment, ...]]:
    merged = dict(stored)
    for comment_id, attachments in live.items():
        merged.setdefault(comment_id, attachments)
    return merged


def _build_event_timeline_item(
    event: EventEntity,
    *,
    attachments: tuple[TimelineAttachment, ...] = (),
) -> TicketTimelineItem:
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
        attachments=attachments,
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
        attachments=tuple(_timeline_attachment(item) for item in comment.attachments),
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
    attachments_by_comment_id: dict[str, tuple[TimelineAttachment, ...]],
) -> list[TicketTimelineItem]:
    items = [
        *(
            _build_event_timeline_item(
                event,
                attachments=attachments_by_comment_id.get(event.source_id or "", ()),
            )
            for event in events
        ),
        *(_build_comment_timeline_item(comment) for comment in fallback_comments),
        *(_build_attempt_timeline_item(attempt) for attempt in attempts),
    ]
    return sorted(items, key=lambda item: item.created_at)


@router.get("")
async def get_tickets(  # noqa: PLR0913, PLR0914, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ticket_id: str | None = None,
    status: str | None = None,
    brand: str | None = None,
    observing: str | None = None,
    classification_decision: str | None = None,
    classification_source: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Response:
    selected_limit = parse_page_limit(limit)
    parsed_brand = _parse_brand(brand)
    settings = config.get_app_settings()
    filters = TicketFilters(
        ticket_id_prefix=_parse_ticket_id_prefix(ticket_id),
        status=_parse_status(status),
        brand_id=settings.brand.id_for(parsed_brand) if parsed_brand else None,
        observing=_parse_observing(observing),
        classification_decision=_parse_classification_decision(classification_decision),
        classification_source=_parse_classification_source(classification_source),
    )

    async with session_local() as session:
        repo = TicketsRepository(session)
        result = await repo.list_tickets(
            filters=filters,
            limit=selected_limit,
            offset=offset,
        )

    csrf = session_manager.create_csrf_token()
    selected_ticket_id = ticket_id or ""
    selected_status = status or ""
    selected_brand = brand or ""
    selected_observing = observing or ""
    selected_classification_decision = classification_decision or ""
    selected_classification_source = classification_source or ""
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
            "selected_classification_decision": selected_classification_decision,
            "selected_classification_source": selected_classification_source,
            "statuses": list(TicketStatus),
            "brands": list(Brand),
            "observing_options": OBSERVING_OPTIONS,
            "classification_decision_options": CLASSIFICATION_DECISION_OPTIONS,
            "classification_source_options": CLASSIFICATION_SOURCE_OPTIONS,
            "limit_options": PAGE_LIMIT_OPTIONS,
            "limit": result.limit,
            "offset": result.offset,
            "prev_url": _tickets_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                brand=selected_brand,
                observing=selected_observing,
                classification_decision=selected_classification_decision,
                classification_source=selected_classification_source,
                limit=result.limit,
                offset=prev_offset,
            ),
            "next_url": _tickets_url(
                ticket_id=selected_ticket_id,
                status=selected_status,
                brand=selected_brand,
                observing=selected_observing,
                classification_decision=selected_classification_decision,
                classification_source=selected_classification_source,
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
async def get_ticket_detail(  # noqa: PLR0913, PLR0914, PLR0917
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
        classification_audits_repo = TicketClassificationAuditsRepository(session)
        attachments_repo = TicketCommentAttachmentsRepository(session)

        try:
            ticket = await tickets_repo.get_ticket_by_id(ticket_id)
        except TicketNotFound:
            ticket = None

        events = await events_repo.list_by_ticket(ticket_id)
        comment_events = [event for event in events if event.source_type == "comment"]
        attempts = await attempts_repo.list_by_ticket(ticket_id)
        classification_audit = await classification_audits_repo.get_latest_by_ticket(ticket_id)
        classification_audits = await classification_audits_repo.list_by_ticket(ticket_id)
        stored_attachments = await attachments_repo.list_by_ticket(ticket_id)

    fallback_comments: list[Comment] = []
    comments_error = None
    comments_source = "local"
    live_comments: list[Comment] = []
    if not comment_events:
        live_comments, comments_error = await _load_zendesk_comments(ticket_id)
        fallback_comments = live_comments
        comments_source = "zendesk_fallback"
    else:
        live_comments, _ = await _load_zendesk_comments(ticket_id)

    timeline_items = _build_ticket_timeline(
        events=events,
        fallback_comments=fallback_comments,
        attempts=attempts,
        attachments_by_comment_id=_merge_comment_attachments(
            _stored_comment_attachments(stored_attachments),
            _comment_attachments(live_comments),
        ),
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
            "classification_audit": classification_audit,
            "classification_audits": classification_audits,
            "manual_classification_decision_options": MANUAL_CLASSIFICATION_DECISION_OPTIONS,
            "filter_rule_kinds": list(TicketsFilterRuleKind),
            "can_edit_classification": user.role.level >= UserRole.ADMIN.level,
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


@router.post("/{ticket_id}/classification")
async def update_ticket_classification(
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    decision: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
) -> Response:
    parsed_decision = _parse_classification_decision(decision)
    if parsed_decision is None:
        return RedirectResponse(
            url=f"/admin/tickets/{ticket_id}?error=classification_decision",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with session_local() as session:
        async with session.begin():
            tickets_repo = TicketsRepository(session)
            classification_audits_repo = TicketClassificationAuditsRepository(session)
            try:
                ticket = await tickets_repo.get_ticket_by_id(ticket_id)
            except TicketNotFound:
                return RedirectResponse(
                    url=f"/admin/tickets/{ticket_id}?error=ticket_not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            await classification_audits_repo.create(
                TicketClassificationAuditCreate(
                    ticket_id=ticket.ticket_id,
                    brand_id=ticket.brand_id,
                    decision=parsed_decision,
                    source=CLASSIFICATION_SOURCE_MANUAL,
                    detail=_manual_classification_detail(user.username, note),
                ),
            )

    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}?saved=classification",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{ticket_id}/classification/run")
async def run_ticket_classification(
    ticket_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    del user

    zendesk_ticket, load_error = await _load_zendesk_ticket(ticket_id)
    if load_error is not None or zendesk_ticket is None:
        return RedirectResponse(
            url=f"/admin/tickets/{ticket_id}?error=zendesk_ticket",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with session_local() as session:
        async with session.begin():
            tickets_repo = TicketsRepository(session)
            try:
                local_ticket = await tickets_repo.get_ticket_by_id(ticket_id)
            except TicketNotFound:
                return RedirectResponse(
                    url=f"/admin/tickets/{ticket_id}?error=ticket_not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            settings = config.get_app_settings()
            brand_id = zendesk_ticket.brand_id or local_ticket.brand_id
            brand = settings.brand.brand_for_id(brand_id) if brand_id else None
            if brand is None:
                return RedirectResponse(
                    url=f"/admin/tickets/{ticket_id}?error=zendesk_ticket",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            zendesk_ticket.id = zendesk_ticket.id or ticket_id
            zendesk_ticket.brand_id = brand_id

            service = TicketClassificationService(request.app.state.llm_context)
            result = await service.classify_and_store(
                session,
                ticket=zendesk_ticket,
                brand=brand,
                force=True,
            )

    if result.decision == CLASSIFICATION_DECISION_CUSTOMER:
        comments, comments_error = await _load_zendesk_comments(ticket_id)
        if comments_error is None:
            async with session_local() as session:
                async with session.begin():
                    await store_ticket_comment_attachments(session, ticket_id=ticket_id, comments=comments)

    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}?saved=auto_classification",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{ticket_id}/filter-rules")
async def create_ticket_filter_rule(  # noqa: PLR0913, PLR0917
    ticket_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    kind: Annotated[str, Form()],
    value: Annotated[str, Form()],
    is_regex: Annotated[str | None, Form()] = None,
    via_channel: Annotated[str | None, Form()] = None,
    comment: Annotated[str | None, Form()] = None,
) -> Response:
    parsed_kind = _parse_filter_rule_kind(kind)
    if parsed_kind is None:
        return RedirectResponse(
            url=f"/admin/tickets/{ticket_id}?error=filter_rule_kind",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    regex_enabled = is_regex == "true"
    normalized_value = value.strip()
    validation_error = _validate_filter_rule_value(normalized_value, is_regex=regex_enabled)
    if validation_error is not None:
        return RedirectResponse(
            url=f"/admin/tickets/{ticket_id}?error={validation_error}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with session_local() as session:
        async with session.begin():
            tickets_repo = TicketsRepository(session)
            filter_rules_repo = TicketsFilterRuleRepository(session)
            try:
                ticket = await tickets_repo.get_ticket_by_id(ticket_id)
            except TicketNotFound:
                return RedirectResponse(
                    url=f"/admin/tickets/{ticket_id}?error=ticket_not_found",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            await filter_rules_repo.create_rule(
                TicketsFilterRuleDTO(
                    kind=parsed_kind,
                    value=normalized_value,
                    is_regex=regex_enabled,
                    brand_id=ticket.brand_id,
                    via_channel=_normalize_via_channel(via_channel),
                    comment=_normalize_optional(comment),
                    created_by=user.username,
                ),
            )
            await _touch_filter_checkpoints(session)

    return RedirectResponse(
        url=f"/admin/tickets/{ticket_id}?saved=filter_rule",
        status_code=status.HTTP_303_SEE_OTHER,
    )
