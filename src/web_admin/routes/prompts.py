from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from src.admin.services import PromptAdminService
from src.ai.config.prompt import LLMPrompt
from src.db.models import AdminUser as AdminUserEntity, LLMPromptKey, UserRole
from src.libs.zendesk_client.models import Brand
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/prompts", tags=["prompts"])


@dataclass(frozen=True)
class PromptSummary:
    brand: Brand
    key: LLMPromptKey
    length: int
    updated_by: str
    updated_at: datetime | None


def _build_flash(saved: str | None, error: str | None) -> dict[str, str] | None:
    if saved:
        return {"kind": "success", "message": "Prompt updated."}
    if error == "selection":
        return {"kind": "error", "message": "Invalid prompt selection."}
    if error == "save":
        return {"kind": "error", "message": "Prompt update failed."}
    return None


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _brand_or_none(value: str | int | None) -> Brand | None:
    if value in {None, ""}:
        return None
    try:
        brand = Brand(int(value))
    except (TypeError, ValueError):
        return None
    return brand if brand in Brand.supported() else None


def _key_or_none(value: str | None) -> LLMPromptKey | None:
    if not value:
        return None
    try:
        return LLMPromptKey(value)
    except ValueError:
        return None


def _prompt_url(
    *,
    brand: Brand,
    key: LLMPromptKey,
    saved: bool = False,
    error: str | None = None,
) -> str:
    url = f"/admin/prompts?brand={brand.value}&key={key.value}"
    if saved:
        return f"{url}&saved=1"
    if error:
        return f"{url}&error={error}"
    return url


async def _load_prompt_page(
    service: PromptAdminService,
    selected_brand: Brand,
    selected_key: LLMPromptKey,
) -> tuple[list[PromptSummary], LLMPrompt]:
    summaries: list[PromptSummary] = []
    selected_prompt: LLMPrompt | None = None

    for item in service.list_prompt_keys():
        prompt = await service.get_prompt(item.brand, item.key)
        summaries.append(
            PromptSummary(
                brand=item.brand,
                key=item.key,
                length=len(prompt.text or ""),
                updated_by=prompt.updated_by,
                updated_at=prompt.updated_at,
            ),
        )
        if item.brand == selected_brand and item.key == selected_key:
            selected_prompt = prompt

    if selected_prompt is None:
        selected_prompt = await service.get_prompt(selected_brand, selected_key)

    return summaries, selected_prompt


@router.get("")
async def get_prompts(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    brand: str | None = None,
    key: str | None = None,
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    brands = Brand.supported()
    keys = list(LLMPromptKey)
    selected_brand = _brand_or_none(brand) or brands[0]
    selected_key = _key_or_none(key) or keys[0]

    async with PromptAdminService() as service:
        summaries, prompt = await _load_prompt_page(service, selected_brand, selected_key)

    csrf = session_manager.create_csrf_token()
    response = templates.TemplateResponse(
        request,
        "prompts.html",
        {
            "active_page": "prompts",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": _build_flash(saved, error),
            "brands": brands,
            "keys": keys,
            "summaries": summaries,
            "selected_brand": selected_brand,
            "selected_key": selected_key,
            "prompt": prompt,
            "can_edit": user.role.level >= UserRole.SUPERADMIN.level,
            "format_datetime": _format_datetime,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.get("/export")
async def export_prompt(
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    brand: str,
    key: str,
) -> Response:
    selected_brand = _brand_or_none(brand)
    selected_key = _key_or_none(key)
    if selected_brand is None or selected_key is None:
        return RedirectResponse(
            url="/admin/prompts?error=selection",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with PromptAdminService() as service:
        exported = await service.export_prompt(selected_brand, selected_key)

    return Response(
        content=exported.content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )


@router.post("")
async def update_prompt(
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    brand: Annotated[str, Form()],
    key: Annotated[str, Form()],
    text: Annotated[str, Form()],
    comment: Annotated[str | None, Form()] = None,
) -> Response:
    selected_brand = _brand_or_none(brand)
    selected_key = _key_or_none(key)
    if selected_brand is None or selected_key is None:
        return RedirectResponse(
            url="/admin/prompts?error=selection",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        async with PromptAdminService() as service:
            await service.update_prompt(
                brand=selected_brand,
                key=selected_key,
                text=text,
                updated_by=user.username,
                comment=comment.strip() or None if comment else None,
            )
    except Exception:
        return RedirectResponse(
            url=_prompt_url(brand=selected_brand, key=selected_key, error="save"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_prompt_url(brand=selected_brand, key=selected_key, saved=True),
        status_code=status.HTTP_303_SEE_OTHER,
    )
