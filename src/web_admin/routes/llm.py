from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from src.admin.services import ClassificationSettingsPatch, LLMAdminService, ResponseSettingsPatch
from src.config import LLMProvider
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates

router = APIRouter(prefix="/llm", tags=["llm"])

SUPPORTED_MODELS_BY_PROVIDER: dict[str, list[str]] = {
    LLMProvider.GOOGLE.value: [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
    ],
}
SUPPORTED_PROVIDERS: dict[str, str] = {
    LLMProvider.GOOGLE.value: "Google",
}


def _build_flash(saved: str | None, error: str | None) -> dict[str, str] | None:
    if saved == "response":
        return {"kind": "success", "message": "Response settings updated."}
    if saved == "classification":
        return {"kind": "success", "message": "Classification settings updated."}
    if error == "validation":
        return {"kind": "error", "message": "Invalid settings value."}
    if error == "provider":
        return {"kind": "error", "message": "Only Google provider is currently supported."}
    if error == "model":
        return {"kind": "error", "message": "Unsupported model for the selected provider."}
    return None


@router.get("")
async def get_llm_settings(
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    saved: str | None = None,
    error: str | None = None,
) -> Response:
    async with LLMAdminService() as service:
        settings = await service.get_settings()

    csrf = session_manager.prepare_csrf(request)
    response = templates.TemplateResponse(
        request,
        "llm_settings.html",
        {
            "active_page": "llm",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": _build_flash(saved, error),
            "response": settings.response,
            "classification": settings.classification,
            "supported_models_by_provider": SUPPORTED_MODELS_BY_PROVIDER,
            "supported_providers": SUPPORTED_PROVIDERS,
            "default_provider": LLMProvider.GOOGLE.value,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


def _provider_or_none(value: str | None) -> LLMProvider | None:
    if not value:
        return None
    return LLMProvider(value)


def _model_or_none(provider: LLMProvider | None, value: str | None) -> str | None:
    model = value.strip() if value else None
    if not model:
        return None

    provider_value = (provider or LLMProvider.GOOGLE).value
    if model not in SUPPORTED_MODELS_BY_PROVIDER.get(provider_value, []):
        raise ValueError("Unsupported model for provider")

    return model


@router.post("/response")
async def update_response_settings(
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    temperature: Annotated[float, Form()],
    top_p: Annotated[float, Form()],
    max_tokens: Annotated[int, Form()],
    provider: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        try:
            parsed_provider = _provider_or_none(provider)
        except ValueError:
            return RedirectResponse(
                url="/admin/llm?error=provider",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        patch = ResponseSettingsPatch(
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            provider=parsed_provider,
            model=_model_or_none(parsed_provider, model),
        )
        async with LLMAdminService() as service:
            await service.update_response_settings(patch, updated_by=user.username)
    except ValidationError:
        return RedirectResponse(
            url="/admin/llm?error=validation",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ValueError:
        return RedirectResponse(
            url="/admin/llm?error=model",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url="/admin/llm?saved=response",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/classification")
async def update_classification_settings(
    enabled: Annotated[str, Form()],
    threshold: Annotated[float, Form()],
    temperature: Annotated[float, Form()],
    top_p: Annotated[float, Form()],
    max_tokens: Annotated[int, Form()],
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    _: Annotated[None, Depends(require_csrf)],
) -> Response:
    try:
        patch = ClassificationSettingsPatch(
            enabled=enabled.lower() == "true",
            threshold=threshold,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        async with LLMAdminService() as service:
            await service.update_classification_settings(patch, updated_by=user.username)
    except ValidationError:
        return RedirectResponse(
            url="/admin/llm?error=validation",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url="/admin/llm?saved=classification",
        status_code=status.HTTP_303_SEE_OTHER,
    )
