from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import MerchantListing as MerchantListingEntity
from src.db.repositories.base import BaseRepository
from src.workflows.schemes import MerchantListingRow


class MerchantListingNotExists(Exception):
    def __init__(self, listing_id: int) -> None:
        super().__init__(f"Merchant listing {listing_id} not found")
        self.listing_id = listing_id


@dataclass(frozen=True)
class MerchantListingFilters:
    brand_id: int
    marketplace_id: str | None = None
    search: str | None = None


@dataclass(frozen=True)
class MerchantListingListResult:
    items: list[MerchantListingEntity]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class MerchantListingMarketplaceSummary:
    marketplace_id: str
    total: int
    updated_at: datetime | None


def _build_search_text(item_name: str, item_description: str | None) -> str:
    item_name = (item_name or "").strip()
    if item_description:
        return f"{item_name}\n\n{item_description.strip()}"
    return item_name


def _merge_text_field(incoming: str | None, existing: str | None) -> str:
    incoming_value = (incoming or "").strip()
    existing_value = (existing or "").strip()
    return incoming_value if incoming_value else existing_value


def _merge_optional_field(incoming: str | None, existing: str | None) -> str | None:
    incoming_value = (incoming or "").strip() or None
    existing_value = (existing or "").strip() or None
    return incoming_value if incoming_value is not None else existing_value


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
        if not rows:
            self.logger.warning(
                "upsert_many.skip_empty",
                extra={"brand_id": brand_id, "marketplace_id": marketplace_id},
            )
            return

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
                item_name = row.item_name or ""
                item_description = row.item_description
                entity = MerchantListingEntity(
                    brand_id=brand_id,
                    marketplace_id=marketplace_id,
                    asin=row.asin,
                    seller_sku=row.seller_sku,
                    item_name=item_name,
                    item_description=item_description,
                    fulfillment_channel=row.fulfillment_channel,
                    search_text=row.search_text or _build_search_text(item_name, item_description),
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(entity)
                existing[key] = entity
            else:
                item_name = _merge_text_field(row.item_name, entity.item_name)
                item_description = _merge_optional_field(row.item_description, entity.item_description)
                fulfillment_channel = _merge_optional_field(
                    row.fulfillment_channel,
                    entity.fulfillment_channel,
                )
                entity.item_name = item_name
                entity.item_description = item_description
                entity.fulfillment_channel = fulfillment_channel
                entity.search_text = row.search_text or _build_search_text(item_name, item_description)
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

    async def search_by_text(
        self,
        *,
        brand_id: int,
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
            .where(MerchantListingEntity.search_tsv.op("@@")(ts_query))
            .order_by(func.ts_rank_cd(MerchantListingEntity.search_tsv, ts_query).desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.all()
        # rows: list[(MerchantListingEntity, rank)]
        return [(row[0], float(row[1])) for row in rows]

    async def search_by_asin(
        self,
        *,
        brand_id: int,
        asin: str,
    ) -> list[MerchantListingEntity]:
        stmt = (
            select(MerchantListingEntity)
            .where(MerchantListingEntity.brand_id == brand_id)
            .where(MerchantListingEntity.asin == asin)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return list(rows)

    async def list_items(
        self,
        *,
        filters: MerchantListingFilters,
        limit: int,
        offset: int,
    ) -> MerchantListingListResult:
        where_clauses = [MerchantListingEntity.brand_id == filters.brand_id]
        if filters.marketplace_id:
            where_clauses.append(MerchantListingEntity.marketplace_id == filters.marketplace_id)

        search = (filters.search or "").strip()
        if search:
            pattern = f"%{search}%"
            where_clauses.append(
                or_(
                    MerchantListingEntity.asin.ilike(pattern),
                    MerchantListingEntity.seller_sku.ilike(pattern),
                    MerchantListingEntity.item_name.ilike(pattern),
                    MerchantListingEntity.item_description.ilike(pattern),
                ),
            )

        total_stmt = select(func.count()).select_from(MerchantListingEntity).where(*where_clauses)
        total = int(await self._session.scalar(total_stmt) or 0)

        stmt = (
            select(MerchantListingEntity)
            .where(*where_clauses)
            .order_by(
                MerchantListingEntity.marketplace_id.asc(),
                MerchantListingEntity.asin.asc(),
                MerchantListingEntity.seller_sku.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return MerchantListingListResult(
            items=list(result.scalars().all()),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_by_id(self, listing_id: int) -> MerchantListingEntity:
        stmt = select(MerchantListingEntity).where(MerchantListingEntity.id == listing_id)
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity is None:
            raise MerchantListingNotExists(listing_id)
        return entity

    async def list_by_asin(
        self,
        *,
        brand_id: int,
        asin: str,
        exclude_id: int | None = None,
    ) -> list[MerchantListingEntity]:
        where_clauses = [
            MerchantListingEntity.brand_id == brand_id,
            MerchantListingEntity.asin == asin,
        ]
        if exclude_id is not None:
            where_clauses.append(MerchantListingEntity.id != exclude_id)

        stmt = (
            select(MerchantListingEntity)
            .where(*where_clauses)
            .order_by(
                MerchantListingEntity.marketplace_id.asc(),
                MerchantListingEntity.seller_sku.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def summarize_by_marketplace(
        self,
        *,
        brand_id: int,
    ) -> list[MerchantListingMarketplaceSummary]:
        stmt = (
            select(
                MerchantListingEntity.marketplace_id,
                func.count(MerchantListingEntity.id),
                func.max(MerchantListingEntity.updated_at),
            )
            .where(MerchantListingEntity.brand_id == brand_id)
            .group_by(MerchantListingEntity.marketplace_id)
        )
        result = await self._session.execute(stmt)
        return [
            MerchantListingMarketplaceSummary(
                marketplace_id=str(row[0]),
                total=int(row[1] or 0),
                updated_at=row[2],
            )
            for row in result.all()
        ]
