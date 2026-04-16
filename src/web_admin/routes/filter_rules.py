from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from src import datetime_utils
from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories import (
    CheckpointsRepository,
    TicketsFilterRuleNotFound,
    TicketsFilterRuleRepository,
)
from src.libs.zendesk_client.models import Brand
from src.tickets_filter.cache import get_checkpoint_name, tickets_filter_cache
from src.tickets_filter.config import TicketsFilterRuleKind
from src.tickets_filter.dto import TicketsFilterRuleDTO
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.pagination import DEFAULT_PAGE_LIMIT, PAGE_LIMIT_OPTIONS, parse_page_limit
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/filter-rules", tags=["filter-rules"])

MAX_RULE_VALUE_LENGTH = 128
DEFAULT_LIMIT = DEFAULT_PAGE_LIMIT
ACTIVE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Any"),
    ("true", "Active"),
    ("false", "Inactive"),
)
SAVED_MESSAGES: dict[str, str] = {
    "created": "Filter rule created.",
    "updated": "Filter rule updated.",
    "active": "Filter rule status updated.",
    "test_match": "Rule matches the sample.",
    "test_no_match": "Rule does not match the sample.",
}
ERROR_MESSAGES: dict[str, str] = {
    "not_found": "Filter rule not found.",
    "kind": "Invalid rule kind.",
    "value": "Rule value is required and must be 128 characters or fewer.",
    "regex": "Invalid regular expression.",
    "brand": "Invalid brand.",
    "active": "Invalid rule status.",
    "sample": "Sample text is required.",
}


def _flash(saved: str | None, error: str | None) -> dict[str, str] | None:
    if saved in SAVED_MESSAGES:
        return {"kind": "success", "message": SAVED_MESSAGES[saved]}
    if error in ERROR_MESSAGES:
        return {"kind": "error", "message": ERROR_MESSAGES[error]}
    return None


def _parse_kind(value: str | None) -> TicketsFilterRuleKind | None:
    if not value:
        return None
    try:
        return TicketsFilterRuleKind(value)
    except ValueError:
        return None


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    try:
        return Brand(int(value))
    except (TypeError, ValueError):
        return None


def _parse_active(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _parse_offset(value: int | None) -> int:
    if value is None or value < 0:
        return 0
    return value


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_via_channel(value: str | None) -> str | None:
    normalized = _normalize_optional(value)
    return normalized.lower() if normalized is not None else None


def _normalize_brand_id(value: str | None) -> int | None:
    brand = _parse_brand(value)
    return brand.value if brand is not None else None


def _validate_rule_value(value: str, *, is_regex: bool) -> str | None:
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_RULE_VALUE_LENGTH:
        return "value"
    if is_regex:
        try:
            re.compile(normalized)
        except re.error:
            return "regex"
    return None


def _rules_url(  # noqa: PLR0913
    *,
    kind: str = "",
    active: str = "",
    brand: str = "",
    search: str = "",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    saved: str | None = None,
    error: str | None = None,
) -> str:
    query = {
        "kind": kind,
        "active": active,
        "brand": brand,
        "search": search,
        "limit": str(limit),
        "offset": str(offset),
    }
    if saved:
        query["saved"] = saved
    if error:
        query["error"] = error
    return f"/admin/filter-rules?{urlencode(query)}"


def _pagination_context(
    *,
    all_rules: list,
    selected_limit: int,
    selected_offset: int,
    url_params: dict[str, str | int],
) -> dict[str, object]:
    total = len(all_rules)
    if 0 < total <= selected_offset:
        selected_offset = max(total - selected_limit, 0)

    has_prev = selected_offset > 0
    has_next = selected_offset + selected_limit < total

    return {
        "rules": all_rules[selected_offset : selected_offset + selected_limit],
        "offset": selected_offset,
        "total": total,
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_url": _rules_url(**url_params, offset=max(selected_offset - selected_limit, 0)),
        "next_url": _rules_url(**url_params, offset=selected_offset + selected_limit),
    }


async def _touch_filter_checkpoints(session) -> None:
    now = datetime_utils.utcnow()
    checkpoints_repo = CheckpointsRepository(session)
    for brand in Brand:
        await checkpoints_repo.set_checkpoint(get_checkpoint_name(brand), now)
    tickets_filter_cache.clear()


@router.get("")
async def get_filter_rules(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    kind: str | None = None,
    active: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    selected_kind = kind or ""
    selected_active = active or ""
    selected_brand = brand or ""
    selected_search = (search or "").strip()
    selected_limit = parse_page_limit(limit)
    selected_offset = _parse_offset(offset)

    async with session_local() as session:
        repo = TicketsFilterRuleRepository(session)
        all_rules = await repo.list_rules(
            kind=_parse_kind(kind).value if _parse_kind(kind) is not None else None,
            brand_id=_parse_brand(brand).value if _parse_brand(brand) is not None else None,
            is_active=_parse_active(active),
            search=selected_search or None,
        )
    url_params = {
        "kind": selected_kind,
        "active": selected_active,
        "brand": selected_brand,
        "search": selected_search,
        "limit": selected_limit,
    }
    pagination = _pagination_context(
        all_rules=all_rules,
        selected_limit=selected_limit,
        selected_offset=selected_offset,
        url_params=url_params,
    )

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "filter_rules.html",
        {
            "active_page": "filter_rules",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": _flash(saved, error),
            "rule_kinds": list(TicketsFilterRuleKind),
            "brands": list(Brand),
            "active_options": ACTIVE_OPTIONS,
            "limit_options": PAGE_LIMIT_OPTIONS,
            "selected_kind": selected_kind,
            "selected_active": selected_active,
            "selected_brand": selected_brand,
            "selected_search": selected_search,
            "selected_limit": selected_limit,
            **pagination,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.post("")
async def create_filter_rule(  # noqa: PLR0913, PLR0917
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    kind: Annotated[str, Form()],
    value: Annotated[str, Form()],
    is_regex: Annotated[str | None, Form()] = None,
    brand_id: Annotated[str | None, Form()] = None,
    via_channel: Annotated[str | None, Form()] = None,
    comment: Annotated[str | None, Form()] = None,
) -> Response:
    parsed_kind = _parse_kind(kind)
    if parsed_kind is None:
        return RedirectResponse(url=_rules_url(error="kind"), status_code=status.HTTP_303_SEE_OTHER)
    if brand_id and _parse_brand(brand_id) is None:
        return RedirectResponse(url=_rules_url(error="brand"), status_code=status.HTTP_303_SEE_OTHER)

    regex_enabled = is_regex == "true"
    normalized_value = value.strip()
    error = _validate_rule_value(normalized_value, is_regex=regex_enabled)
    if error is not None:
        return RedirectResponse(url=_rules_url(error=error), status_code=status.HTTP_303_SEE_OTHER)

    async with session_local() as session:
        async with session.begin():
            repo = TicketsFilterRuleRepository(session)
            await repo.create_rule(
                TicketsFilterRuleDTO(
                    kind=parsed_kind,
                    value=normalized_value,
                    is_regex=regex_enabled,
                    brand_id=_normalize_brand_id(brand_id),
                    via_channel=_normalize_via_channel(via_channel),
                    comment=_normalize_optional(comment),
                    created_by=user.username,
                ),
            )
            await _touch_filter_checkpoints(session)

    return RedirectResponse(url=_rules_url(saved="created"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{rule_id}")
async def update_filter_rule(  # noqa: PLR0913, PLR0917
    rule_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    kind: Annotated[str, Form()],
    value: Annotated[str, Form()],
    is_regex: Annotated[str | None, Form()] = None,
    brand_id: Annotated[str | None, Form()] = None,
    via_channel: Annotated[str | None, Form()] = None,
    comment: Annotated[str | None, Form()] = None,
) -> Response:
    parsed_kind = _parse_kind(kind)
    if parsed_kind is None:
        return RedirectResponse(url=_rules_url(error="kind"), status_code=status.HTTP_303_SEE_OTHER)
    if brand_id and _parse_brand(brand_id) is None:
        return RedirectResponse(url=_rules_url(error="brand"), status_code=status.HTTP_303_SEE_OTHER)

    regex_enabled = is_regex == "true"
    normalized_value = value.strip()
    error = _validate_rule_value(normalized_value, is_regex=regex_enabled)
    if error is not None:
        return RedirectResponse(url=_rules_url(error=error), status_code=status.HTTP_303_SEE_OTHER)

    try:
        async with session_local() as session:
            async with session.begin():
                repo = TicketsFilterRuleRepository(session)
                await repo.update_rule(
                    rule_id,
                    kind=parsed_kind.value,
                    value=normalized_value,
                    is_regex=regex_enabled,
                    brand_id=_normalize_brand_id(brand_id),
                    via_channel=_normalize_via_channel(via_channel),
                    comment=_normalize_optional(comment),
                    updated_by=user.username,
                )
                await _touch_filter_checkpoints(session)
    except TicketsFilterRuleNotFound:
        return RedirectResponse(url=_rules_url(error="not_found"), status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(url=_rules_url(saved="updated"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{rule_id}/active")
async def update_filter_rule_active(
    rule_id: int,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    is_active: Annotated[str, Form()],
) -> Response:
    parsed_active = _parse_active(is_active)
    if parsed_active is None:
        return RedirectResponse(url=_rules_url(error="active"), status_code=status.HTTP_303_SEE_OTHER)

    try:
        async with session_local() as session:
            async with session.begin():
                repo = TicketsFilterRuleRepository(session)
                if parsed_active:
                    await repo.activate_rule(rule_id, updated_by=user.username)
                else:
                    await repo.deactivate_rule(rule_id, updated_by=user.username)
                await _touch_filter_checkpoints(session)
    except TicketsFilterRuleNotFound:
        return RedirectResponse(url=_rules_url(error="not_found"), status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(url=_rules_url(saved="active"), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{rule_id}/test")
async def test_filter_rule(
    rule_id: int,
    _: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    __: Annotated[None, Depends(require_csrf)],
    sample_text: Annotated[str, Form()],
) -> Response:
    normalized_sample = sample_text.strip()
    if not normalized_sample:
        return RedirectResponse(url=_rules_url(error="sample"), status_code=status.HTTP_303_SEE_OTHER)

    try:
        async with session_local() as session:
            repo = TicketsFilterRuleRepository(session)
            rule = await repo.get_rule(rule_id)
    except TicketsFilterRuleNotFound:
        return RedirectResponse(url=_rules_url(error="not_found"), status_code=status.HTTP_303_SEE_OTHER)

    pattern = rule.value if rule.is_regex else re.escape(rule.value)
    try:
        flags = re.IGNORECASE | (re.DOTALL if rule.kind.endswith("body_pattern") else 0)
        matches = re.compile(pattern, flags).search(normalized_sample) is not None
    except re.error:
        return RedirectResponse(url=_rules_url(error="regex"), status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(
        url=_rules_url(saved="test_match" if matches else "test_no_match"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
