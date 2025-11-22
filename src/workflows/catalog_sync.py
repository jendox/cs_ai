import logging

import anyio

from src.db import session_local
from src.db.repositories import AcquireLockError, LocksRepository
from src.db.repositories.merchant_listing import MerchantListingRepository
from src.libs.amazon_client.client import AsyncAmazonClient
from src.libs.amazon_client.enums import MarketplaceId, ReportType
from src.libs.amazon_client.schemes import MerchantListingRow

CATALOG_SYNC_LOCK_TTL = 3600

logger = logging.getLogger(__name__)


async def sync_catalog_for_brand_all_eu_markets(
    brand_id: int,
    amazon_client: AsyncAmazonClient,
) -> None:
    markets = MarketplaceId.eu_marketplaces()

    async with anyio.create_task_group() as tg:
        for market in markets:
            tg.start_soon(_sync_single_marketplace, brand_id, market, amazon_client)


async def _sync_single_marketplace(
    brand_id: int,
    marketplace: MarketplaceId,
    amazon_client: AsyncAmazonClient,
) -> None:
    extra = {"brand_id": brand_id, "marketplace_id": marketplace.value}
    lock_name = f"merchant_listing_sync:{brand_id}:{marketplace.value}"
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
                rows: list[MerchantListingRow] = await amazon_client.get_merchant_listings_all_data(marketplace)
                if not rows:
                    raise ValueError(f"Report {ReportType.GET_MERCHANT_LISTINGS_ALL_DATA.name} is empty")

                repo = MerchantListingRepository(session)
                await repo.upsert_many(
                    brand_id=brand_id,
                    marketplace_id=marketplace.value,
                    rows=rows,
                )
                logger.info("sync_completed", extra=extra)
            except Exception as exc:
                logger.exception("sync_failed", extra={**extra, "error": str(exc)})
            finally:
                await lock_repo.release_lock(name=lock_name, holder=lock_holer)
