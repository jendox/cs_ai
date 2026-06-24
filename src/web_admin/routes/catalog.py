from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from src import config
from src.ai.context import LLMContext
from src.brands import Brand
from src.db import session_local
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.db.repositories import MerchantListingFilters, MerchantListingNotExists, MerchantListingRepository
from src.web_admin.catalog_product_view import build_catalog_product_sections
from src.web_admin.catalog_sync_manager import CatalogSyncManager
from src.web_admin.dependencies import get_session_manager, require_csrf, require_role
from src.web_admin.pagination import DEFAULT_PAGE_LIMIT, PAGE_LIMIT_OPTIONS, parse_page_limit
from src.web_admin.session import SessionManager
from src.web_admin.templates import templates
from src.workflows.schemes import MarketplaceId

router = APIRouter(prefix="/catalog", tags=["catalog"])
logger = logging.getLogger("web_admin.catalog")

DEFAULT_LIMIT = DEFAULT_PAGE_LIMIT
EU_MARKETPLACES: tuple[MarketplaceId, ...] = tuple(
    marketplace for marketplace in MarketplaceId if marketplace != MarketplaceId.US
)

MARKETPLACE_LABELS: dict[MarketplaceId, str] = {
    MarketplaceId.UK: "UK",
    MarketplaceId.DE: "DE",
    MarketplaceId.FR: "FR",
    MarketplaceId.IT: "IT",
    MarketplaceId.ES: "ES",
}

AMAZON_DOMAINS: dict[MarketplaceId, str] = {
    MarketplaceId.UK: "www.amazon.co.uk",
    MarketplaceId.DE: "www.amazon.de",
    MarketplaceId.FR: "www.amazon.fr",
    MarketplaceId.IT: "www.amazon.it",
    MarketplaceId.ES: "www.amazon.es",
}

SAVED_MESSAGES: dict[str, str] = {
    "sync_started": "Catalog sync started in the background.",
    "sync_started_marketplace": "Marketplace catalog sync started in the background.",
}
ERROR_MESSAGES: dict[str, str] = {
    "brand": "Invalid brand.",
    "sync_marketplace": "Invalid marketplace for sync.",
    "sync_in_progress": "Catalog sync is already running for this brand.",
    "sync_start": "Could not start catalog sync. Check logs for details.",
    "not_found": "Catalog listing not found.",
}


@dataclass(frozen=True)
class MarketplaceTab:
    marketplace: MarketplaceId | None
    sync_marketplace: str | None
    label: str
    total: int
    updated_at: datetime | None
    url: str
    is_active: bool


@dataclass(frozen=True)
class CatalogUrlParams:
    brand: str
    marketplace: str
    search: str
    limit: int
    offset: int
    saved: str | None = None
    error: str | None = None


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    try:
        brand = Brand(value)
    except ValueError:
        return None
    supported = config.get_app_settings().brand.supported
    return brand if brand in supported else None


def _parse_marketplace(value: str | None) -> MarketplaceId | None:
    if not value:
        return None
    try:
        marketplace = MarketplaceId(value)
    except ValueError:
        return None
    return marketplace if marketplace in EU_MARKETPLACES else None


def _normalize_search(value: str | None) -> str:
    return (value or "").strip()


def _parse_offset(value: int | None) -> int:
    if value is None or value < 0:
        return 0
    return value


def _llm_context(request: Request) -> LLMContext:
    return request.app.state.llm_context


def _catalog_sync_manager(request: Request) -> CatalogSyncManager:
    return request.app.state.catalog_sync_manager


def _flash(saved: str | None, error: str | None, *, marketplace_label: str | None = None) -> dict[str, str] | None:
    if saved == "sync_started_marketplace" and marketplace_label:
        return {
            "kind": "success",
            "message": f"{marketplace_label} catalog sync started in the background.",
        }
    if saved in SAVED_MESSAGES:
        return {"kind": "success", "message": SAVED_MESSAGES[saved]}
    if error in ERROR_MESSAGES:
        return {"kind": "error", "message": ERROR_MESSAGES[error]}
    return None


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _marketplace_label(value: MarketplaceId | str | None) -> str:
    if value is None:
        return "All EU"
    try:
        marketplace = value if isinstance(value, MarketplaceId) else MarketplaceId(value)
    except ValueError:
        return str(value)
    return MARKETPLACE_LABELS.get(marketplace, marketplace.name)


def _amazon_listing_url(marketplace_id: str, asin: str) -> str | None:
    try:
        marketplace = MarketplaceId(marketplace_id)
    except ValueError:
        return None
    domain = AMAZON_DOMAINS.get(marketplace)
    if domain is None:
        return None
    return f"https://{domain}/dp/{asin}"


def _catalog_url(params: CatalogUrlParams) -> str:
    query = {
        "brand": params.brand,
        "marketplace": params.marketplace,
        "search": params.search,
        "limit": str(params.limit),
        "offset": str(params.offset),
    }
    if params.saved:
        query["saved"] = params.saved
    if params.error:
        query["error"] = params.error
    return f"/admin/catalog?{urlencode(query)}"


def _catalog_detail_url(listing_id: int, *, return_url: str | None = None) -> str:
    url = f"/admin/catalog/{listing_id}"
    if return_url:
        return f"{url}?{urlencode({'return_url': return_url})}"
    return url


def _build_tabs(
    *,
    brand: str,
    selected_marketplace: str,
    search: str,
    limit: int,
    summaries_by_marketplace: dict[str, tuple[int, datetime | None]],
) -> list[MarketplaceTab]:
    total_all = sum(total for total, _ in summaries_by_marketplace.values())
    updated_all = max(
        (updated_at for _, updated_at in summaries_by_marketplace.values() if updated_at is not None),
        default=None,
    )
    tabs = [
        MarketplaceTab(
            marketplace=None,
            sync_marketplace=None,
            label="All EU",
            total=total_all,
            updated_at=updated_all,
            url=_catalog_url(
                CatalogUrlParams(
                    brand=brand,
                    marketplace="",
                    search=search,
                    limit=limit,
                    offset=0,
                ),
            ),
            is_active=not selected_marketplace,
        ),
    ]

    for marketplace in EU_MARKETPLACES:
        total, updated_at = summaries_by_marketplace.get(marketplace.value, (0, None))
        tabs.append(
            MarketplaceTab(
                marketplace=marketplace,
                sync_marketplace=marketplace.value,
                label=_marketplace_label(marketplace),
                total=total,
                updated_at=updated_at,
                url=_catalog_url(
                    CatalogUrlParams(
                        brand=brand,
                        marketplace=marketplace.value,
                        search=search,
                        limit=limit,
                        offset=0,
                    ),
                ),
                is_active=selected_marketplace == marketplace.value,
            ),
        )
    return tabs


@router.get("")
async def get_catalog(  # noqa: PLR0913, PLR0914, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    catalog_sync_manager: Annotated[CatalogSyncManager, Depends(_catalog_sync_manager)],
    brand: str | None = None,
    marketplace: str | None = None,
    search: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    saved: str | None = None,
    error: str | None = None,
    sync_marketplace: str | None = None,
) -> Response:
    settings = config.get_app_settings()
    selected_brand = _parse_brand(brand) or settings.brand.supported[0]
    selected_marketplace = _parse_marketplace(marketplace)
    selected_search = _normalize_search(search)
    selected_limit = parse_page_limit(limit)
    selected_offset = _parse_offset(offset)
    brand_id = settings.brand.id_for(selected_brand)

    async with session_local() as session:
        repo = MerchantListingRepository(session)
        result = await repo.list_items(
            filters=MerchantListingFilters(
                brand_id=brand_id,
                marketplace_id=selected_marketplace.value if selected_marketplace else None,
                search=selected_search or None,
            ),
            limit=selected_limit,
            offset=selected_offset,
        )
        summaries = await repo.summarize_by_marketplace(brand_id=brand_id)

    selected_marketplace_value = selected_marketplace.value if selected_marketplace else ""
    summaries_by_marketplace = {
        item.marketplace_id: (item.total, item.updated_at)
        for item in summaries
    }
    prev_offset = max(result.offset - result.limit, 0)
    next_offset = result.offset + result.limit
    list_url = _catalog_url(
        CatalogUrlParams(
            brand=selected_brand.value,
            marketplace=selected_marketplace_value,
            search=selected_search,
            limit=result.limit,
            offset=result.offset,
        ),
    )
    sync_state = catalog_sync_manager.get_state(selected_brand)
    sync_state_payload = sync_state.to_dict() if sync_state else None
    if sync_state is not None and sync_state.status != "running":
        catalog_sync_manager.clear_state(selected_brand)
    flash_marketplace = _parse_marketplace(sync_marketplace) if sync_marketplace else None
    csrf = session_manager.prepare_csrf(request)
    response = templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "active_page": "catalog",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": _flash(
                saved,
                error,
                marketplace_label=_marketplace_label(flash_marketplace) if flash_marketplace else None,
            ),
            "can_sync": user.role.level >= UserRole.SUPERADMIN.level,
            "sync_state": sync_state_payload,
            "brands": settings.brand.supported,
            "marketplaces": EU_MARKETPLACES,
            "marketplace_tabs": _build_tabs(
                brand=selected_brand.value,
                selected_marketplace=selected_marketplace_value,
                search=selected_search,
                limit=result.limit,
                summaries_by_marketplace=summaries_by_marketplace,
            ),
            "result": result,
            "selected_brand": selected_brand.value,
            "selected_marketplace": selected_marketplace_value,
            "selected_search": selected_search,
            "limit_options": PAGE_LIMIT_OPTIONS,
            "limit": result.limit,
            "offset": result.offset,
            "prev_url": _catalog_url(
                CatalogUrlParams(
                    brand=selected_brand.value,
                    marketplace=selected_marketplace_value,
                    search=selected_search,
                    limit=result.limit,
                    offset=prev_offset,
                ),
            ),
            "next_url": _catalog_url(
                CatalogUrlParams(
                    brand=selected_brand.value,
                    marketplace=selected_marketplace_value,
                    search=selected_search,
                    limit=result.limit,
                    offset=next_offset,
                ),
            ),
            "has_prev": result.offset > 0,
            "has_next": result.offset + result.limit < result.total,
            "catalog_detail_url": lambda listing_id: _catalog_detail_url(listing_id, return_url=list_url),
            "format_datetime": _format_datetime,
            "marketplace_label": _marketplace_label,
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response


@router.get("/sync/status")
async def get_catalog_sync_status(
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    catalog_sync_manager: Annotated[CatalogSyncManager, Depends(_catalog_sync_manager)],
    brand: str | None = None,
) -> JSONResponse:
    settings = config.get_app_settings()
    selected_brand = _parse_brand(brand) or settings.brand.supported[0]
    sync_state = catalog_sync_manager.get_state(selected_brand)
    return JSONResponse(
        {
            "brand": selected_brand.value,
            "can_sync": user.role.level >= UserRole.SUPERADMIN.level,
            "sync": sync_state.to_dict() if sync_state else None,
        },
    )


@router.post("/sync")
async def sync_catalog(  # noqa: PLR0913, PLR0917
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.SUPERADMIN))],
    _: Annotated[None, Depends(require_csrf)],
    catalog_sync_manager: Annotated[CatalogSyncManager, Depends(_catalog_sync_manager)],
    brand: Annotated[str, Form()],
    marketplace: Annotated[str | None, Form()] = None,
    sync_marketplace: Annotated[str | None, Form()] = None,
    search: Annotated[str | None, Form()] = None,
    limit: Annotated[int, Form()] = DEFAULT_LIMIT,
) -> Response:
    selected_brand = _parse_brand(brand)
    selected_marketplace = _parse_marketplace(marketplace)
    selected_sync_marketplace = _parse_marketplace(sync_marketplace)
    selected_search = _normalize_search(search)
    selected_limit = parse_page_limit(limit)

    if selected_brand is None:
        return RedirectResponse(
            url="/admin/catalog?error=brand",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if sync_marketplace and selected_sync_marketplace is None:
        return RedirectResponse(
            url="/admin/catalog?error=sync_marketplace",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    redirect_url = _catalog_url(
        CatalogUrlParams(
            brand=selected_brand.value,
            marketplace=selected_marketplace.value if selected_marketplace else "",
            search=selected_search,
            limit=selected_limit,
            offset=0,
        ),
    )

    sync_marketplaces = [selected_sync_marketplace] if selected_sync_marketplace else None

    try:
        result = await catalog_sync_manager.start(
            selected_brand,
            _llm_context(request).amazon_mcp_client,
            started_by=user.username,
            marketplaces=sync_marketplaces,
        )
    except Exception as exc:
        logger.exception(
            "catalog_sync.start_failed",
            extra={"brand": selected_brand.value, "username": user.username, "error": str(exc)},
        )
        return RedirectResponse(
            url=f"{redirect_url}&error=sync_start",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if result == "already_running":
        return RedirectResponse(
            url=f"{redirect_url}&error=sync_in_progress",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    logger.info(
        "catalog_sync.started",
        extra={
            "brand": selected_brand.value,
            "username": user.username,
            "marketplaces": [
                marketplace.value for marketplace in sync_marketplaces
            ] if sync_marketplaces else "all_eu",
        },
    )
    saved_key = "sync_started_marketplace" if selected_sync_marketplace else "sync_started"
    redirect_with_saved = f"{redirect_url}&saved={saved_key}"
    if selected_sync_marketplace:
        redirect_with_saved = f"{redirect_with_saved}&sync_marketplace={selected_sync_marketplace.value}"
    return RedirectResponse(
        url=redirect_with_saved,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{listing_id}")
async def get_catalog_detail(
    listing_id: int,
    request: Request,
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.ADMIN))],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    return_url: str | None = None,
) -> Response:
    async with session_local() as session:
        repo = MerchantListingRepository(session)
        try:
            listing = await repo.get_by_id(listing_id)
        except MerchantListingNotExists:
            return RedirectResponse(
                url="/admin/catalog?error=not_found",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        related_listings = await repo.list_by_asin(
            brand_id=listing.brand_id,
            asin=listing.asin,
            exclude_id=listing.id,
        )

    product_attributes = None
    product_error = None
    try:
        product_attributes = await _llm_context(request).amazon_mcp_client.get_catalog_item_attributes(
            asin=listing.asin,
            marketplace_id=listing.marketplace_id,
        )
    except Exception as exc:
        logger.warning(
            "catalog_detail.product_attributes_failed",
            extra={
                "listing_id": listing_id,
                "asin": listing.asin,
                "marketplace_id": listing.marketplace_id,
                "error": str(exc),
            },
        )
        product_error = "Could not load live Amazon catalog attributes. MCP may be unavailable."

    safe_return_url = return_url if return_url and return_url.startswith("/admin/catalog") else "/admin/catalog"
    csrf = session_manager.prepare_csrf(request)
    response = templates.TemplateResponse(
        request,
        "catalog_detail.html",
        {
            "active_page": "catalog",
            "current_user": user,
            "csrf_token": csrf.raw,
            "flash": None,
            "listing": listing,
            "related_listings": related_listings,
            "product_sections": build_catalog_product_sections(product_attributes),
            "product_error": product_error,
            "return_url": safe_return_url,
            "amazon_listing_url": _amazon_listing_url(listing.marketplace_id, listing.asin),
            "format_datetime": _format_datetime,
            "marketplace_label": _marketplace_label,
            "catalog_detail_url": lambda item_id: _catalog_detail_url(item_id, return_url=safe_return_url),
        },
    )
    session_manager.set_csrf_cookie(response, csrf)
    return response
