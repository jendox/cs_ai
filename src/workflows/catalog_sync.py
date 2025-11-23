import logging

import anyio

from src.db import session_local
from src.db.repositories import AcquireLockError, LocksRepository
from src.db.repositories.merchant_listing import MerchantListingRepository

from .schemes import MarketplaceId, MerchantListingRow
from ..ai.amazon_mcp_client import AmazonMCPHttpClient
from ..libs.zendesk_client.models import Brand

CATALOG_SYNC_LOCK_TTL = 3600

logger = logging.getLogger("catalog_sync")


async def sync_catalog_for_brand_all_eu_markets(
    brand: Brand,
    amazon_mcp_client: AmazonMCPHttpClient,
) -> None:
    markets = MarketplaceId.eu_marketplaces()

    async with anyio.create_task_group() as tg:
        for market in markets:
            tg.start_soon(_sync_single_marketplace, brand, market, amazon_mcp_client)


async def _sync_single_marketplace(
    brand: Brand,
    marketplace: MarketplaceId,
    amazon_mcp_client: AmazonMCPHttpClient,
) -> None:
    extra = {"brand_id": brand.value, "marketplace_id": marketplace.value}
    lock_name = f"merchant_listing_sync:{brand.value}:{marketplace.value}"
    lock_holer = "catalog_sync"

    async with session_local() as session:
        lock_repo = LocksRepository(session)
        async with session.begin():
            try:
                await lock_repo.acquire_lock(
                    name=lock_name,
                    holder=lock_holer,
                    ttl_seconds=CATALOG_SYNC_LOCK_TTL,
                )
            except AcquireLockError as exc:
                logger.info("lock_acquire_failure", extra={**extra, "error": str(exc)})
                return
            logger.info("sync_started", extra=extra)

            try:
                rows = await amazon_mcp_client.get_merchant_listings_all_data(str(marketplace.value))

                repo = MerchantListingRepository(session)
                await repo.upsert_many(
                    brand_id=brand.value,
                    marketplace_id=str(marketplace.value),
                    rows=[MerchantListingRow.model_validate(row) for row in rows],
                )
                logger.info("sync_completed", extra=extra)
            except Exception as exc:
                logger.info("sync_failed", extra={**extra, "error": str(exc)})
            finally:
                await lock_repo.release_lock(name=lock_name, holder=lock_holer)
