from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import MerchantListing as MerchantListingEntity
from src.db.repositories.base import BaseRepository
from src.libs.amazon_client.schemes import MerchantListingRow


class MerchantListingNotExists(Exception): ...


def _build_search_text(item_name: str, item_description: str | None) -> str:
    item_name = (item_name or "").strip()
    if item_description:
        return f"{item_name}\n\n{item_description.strip()}"
    return item_name


class MerchantListingRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="merchant_listing_repository", session=session)

    async def _get_existing_for_brand_marketplace(
        self,
        *,
        brand_id: int,
        marketplace_id: str,
    ) -> dict[tuple[str, str], MerchantListingEntity]:
        """
        Загружаем все существующие листинги для пары (brand_id, marketplace_id)
        и кладём в словарь по ключу (asin, seller_sku).
        """
        stmt = (
            select(MerchantListingEntity)
            .where(MerchantListingEntity.brand_id == brand_id)
            .where(MerchantListingEntity.marketplace_id == marketplace_id)
        )
        result = await self._session.execute(stmt)
        existing: dict[tuple[str, str], MerchantListingEntity] = {}
        for entity in result.scalars().all():
            key = (entity.asin, entity.seller_sku)
            existing[key] = entity
        return existing

    async def upsert_many(
        self,
        *,
        brand_id: int,
        marketplace_id: str,
        rows: list[MerchantListingRow],
    ) -> None:
        """
        Принимает список MerchantListingRow и:
        - создаёт/обновляет записи в merchant_listings
        - обновляет search_text
        - пересчитывает search_tsv для этого brand+marketplace
        """
        now = datetime_utils.utcnow()
        # 1. all existing rows for brand+marketplace
        existing = await self._get_existing_for_brand_marketplace(
            brand_id=brand_id,
            marketplace_id=marketplace_id,
        )

        seen_keys: set[tuple[str, str]] = set()

        # 2. upsert new data
        for row in rows:
            key = (row.asin, row.seller_sku)
            seen_keys.add(key)

            entity = existing.get(key)

            if entity is None:
                entity = MerchantListingEntity(
                    brand_id=brand_id,
                    marketplace_id=marketplace_id,
                    asin=row.asin,
                    seller_sku=row.seller_sku,
                    item_name=row.item_name or "",
                    item_description=row.item_description,
                    fulfillment_channel=row.fulfillment_channel,
                    search_text=row.search_text or (row.item_name or ""),
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(entity)
                existing[key] = entity
            else:
                entity.item_name = row.item_name or ""
                entity.item_description = row.item_description
                entity.fulfillment_channel = row.fulfillment_channel
                entity.search_text = row.search_text or (row.item_name or "")
                entity.updated_at = now

        # 3. remove records that doesn't exist in report
        obsolete_keys = set(existing.keys()) - seen_keys
        for key in obsolete_keys:
            entity = existing[key]
            await self._session.delete(entity)

        await self._session.flush()

        # 4. recalculate search_tsv
        stmt_update_tsv = (
            update(MerchantListingEntity)
            .where(MerchantListingEntity.brand_id == brand_id)
            .where(MerchantListingEntity.marketplace_id == marketplace_id)
            .values(
                search_tsv=func.to_tsvector(
                    "english",
                    MerchantListingEntity.search_text,
                ),
            )
        )
        await self._session.execute(stmt_update_tsv)

        await self._session.commit()

    async def search_by_text(
        self,
        *,
        brand_id: int,
        marketplace_id: str,
        query: str,
        limit: int = 5,
    ) -> list[tuple[MerchantListingEntity, float]]:
        """
        Полнотекстовый поиск по search_tsv для заданного бренда и marketplace.

        Возвращает список (entity, rank), отсортированный по релевантности.
        """
        query = (query or "").strip()
        if not query:
            return []

        ts_query = func.plainto_tsquery("english", query)

        stmt = (
            select(
                MerchantListingEntity,
                func.ts_rank_cd(MerchantListingEntity.search_tsv, ts_query).label("rank"),
            )
            .where(MerchantListingEntity.brand_id == brand_id)
            .where(MerchantListingEntity.marketplace_id == marketplace_id)
            .where(MerchantListingEntity.search_tsv.op("@@")(ts_query))
            .order_by(func.ts_rank_cd(MerchantListingEntity.search_tsv, ts_query).desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.all()
        # rows: list[(MerchantListingEntity, rank)]
        return [(row[0], float(row[1])) for row in rows]
