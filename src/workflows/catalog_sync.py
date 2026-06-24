import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import anyio

from src.brands import Brand
from src.config import get_app_settings
from src.db import session_local
from src.db.repositories import AcquireLockError, LocksRepository
from src.db.repositories.merchant_listing import MerchantListingRepository

from .schemes import MarketplaceId, MerchantListingRow
from ..ai.amazon_mcp_client import AmazonMCPHttpClient

CATALOG_SYNC_LOCK_TTL = 3600

logger = logging.getLogger("catalog_sync")

MarketplaceSyncStatus = Literal["completed", "failed", "skipped"]
MarketplaceSyncCallback = Callable[["MarketplaceSyncOutcome"], Awaitable[None] | None]
MarketplaceStartedCallback = Callable[[MarketplaceId], Awaitable[None] | None]


@dataclass(frozen=True)
class MarketplaceSyncOutcome:
    marketplace: MarketplaceId
    status: MarketplaceSyncStatus
    row_count: int = 0
    error: str | None = None


def brand_sync_lock_name(brand_id: int) -> str:
    return f"catalog_sync_brand:{brand_id}"


async def sync_catalog_for_brand_all_eu_markets(
    brand: Brand,
    amazon_mcp_client: AmazonMCPHttpClient,
    *,
    on_marketplace_started: MarketplaceStartedCallback | None = None,
    on_marketplace_complete: MarketplaceSyncCallback | None = None,
) -> list[MarketplaceSyncOutcome]:
    return await sync_catalog_for_brand_marketplaces(
        brand,
        amazon_mcp_client,
        marketplaces=None,
        on_marketplace_started=on_marketplace_started,
        on_marketplace_complete=on_marketplace_complete,
    )


async def sync_catalog_for_brand_marketplaces(
    brand: Brand,
    amazon_mcp_client: AmazonMCPHttpClient,
    *,
    marketplaces: list[MarketplaceId] | None = None,
    on_marketplace_started: MarketplaceStartedCallback | None = None,
    on_marketplace_complete: MarketplaceSyncCallback | None = None,
) -> list[MarketplaceSyncOutcome]:
    markets = sorted(marketplaces or MarketplaceId.eu_marketplaces(), key=lambda item: item.name)
    outcomes: list[MarketplaceSyncOutcome] = []

    async with anyio.create_task_group() as tg:

        async def run_market(marketplace: MarketplaceId) -> None:
            if on_marketplace_started is not None:
                maybe_started = on_marketplace_started(marketplace)
                if maybe_started is not None:
                    await maybe_started
            outcome = await _sync_single_marketplace(brand, marketplace, amazon_mcp_client)
            outcomes.append(outcome)
            if on_marketplace_complete is not None:
                maybe_awaitable = on_marketplace_complete(outcome)
                if maybe_awaitable is not None:
                    await maybe_awaitable

        for market in markets:
            tg.start_soon(run_market, market)

    return sorted(outcomes, key=lambda item: item.marketplace.name)


async def _sync_single_marketplace(
    brand: Brand,
    marketplace: MarketplaceId,
    amazon_mcp_client: AmazonMCPHttpClient,
) -> MarketplaceSyncOutcome:
    brand_id = get_app_settings().brand.id_for(brand)
    extra = {"brand_id": brand_id, "marketplace_id": marketplace.value}
    lock_name = f"merchant_listing_sync:{brand_id}:{marketplace.value}"
    lock_holder = "catalog_sync"

    async with session_local() as session:
        lock_repo = LocksRepository(session)
        async with session.begin():
            try:
                await lock_repo.acquire_lock(
                    name=lock_name,
                    holder=lock_holder,
                    ttl_seconds=CATALOG_SYNC_LOCK_TTL,
                )
            except AcquireLockError as exc:
                logger.info("lock_acquire_failure", extra={**extra, "error": str(exc)})
                return MarketplaceSyncOutcome(
                    marketplace=marketplace,
                    status="skipped",
                    error=str(exc),
                )
            logger.info("sync_started", extra=extra)

            try:
                rows = await amazon_mcp_client.get_merchant_listings_all_data(str(marketplace.value))
                if not rows:
                    logger.warning("sync_empty_report", extra=extra)
                    return MarketplaceSyncOutcome(
                        marketplace=marketplace,
                        status="failed",
                        error="Report returned no listings",
                    )

                repo = MerchantListingRepository(session)
                await repo.upsert_many(
                    brand_id=brand_id,
                    marketplace_id=str(marketplace.value),
                    rows=[MerchantListingRow.model_validate(row) for row in rows],
                )
                logger.info("sync_completed", extra={**extra, "row_count": len(rows)})
                return MarketplaceSyncOutcome(
                    marketplace=marketplace,
                    status="completed",
                    row_count=len(rows),
                )
            except Exception as exc:
                logger.info("sync_failed", extra={**extra, "error": str(exc)})
                return MarketplaceSyncOutcome(
                    marketplace=marketplace,
                    status="failed",
                    error=str(exc),
                )
            finally:
                await lock_repo.release_lock(name=lock_name, holder=lock_holder)
