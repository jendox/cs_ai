import csv
import io

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import MerchantListing as MerchantListingEntity
from src.db.repositories.base import BaseRepository


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

    async def sync_from_tsv(
        self,
        *,
        brand_id: int,
        marketplace_id: str,
        tsv_bytes: bytes,
    ) -> None:
        """
        1) парсит TSV (binary -> text -> DictReader)
        2) делает upsert по (brand_id, marketplace_id, asin1, seller-sku)
        3) создаёт/обновляет search_text
        4) пересчитывает search_tsv через to_tsvector
        """
        now = datetime_utils.utcnow()

        # 1. загружаем существующие записи
        existing = await self._get_existing_for_brand_marketplace(
            brand_id=brand_id,
            marketplace_id=marketplace_id,
        )

        # 2. парсинг TSV
        text = tsv_bytes.decode("utf-8", errors="replace")
        f = io.StringIO(text)
        reader = csv.DictReader(f, delimiter="\t")

        for row in reader:
            asin = (row.get("asin1") or "").strip()
            sku = (row.get("seller-sku") or "").strip()
            if not asin or not sku:
                continue

            item_name = (row.get("item-name") or "").strip()
            # в отчётах иногда пусто, поэтому нормализуем до None
            item_description = (row.get("item-description") or "").strip() or None
            fulfillment_channel = (row.get("fulfillment-channel") or "").strip() or None

            search_text = _build_search_text(item_name, item_description)

            key = (asin, sku)
            entity = existing.get(key)

            if entity is None:
                # создаём новую запись
                entity = MerchantListingEntity(
                    brand_id=brand_id,
                    marketplace_id=marketplace_id,
                    asin=asin,
                    seller_sku=sku,
                    item_name=item_name,
                    item_description=item_description,
                    fulfillment_channel=fulfillment_channel,
                    search_text=search_text,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(entity)
                existing[key] = entity
            else:
                # обновляем существующую
                entity.item_name = item_name
                entity.item_description = item_description
                entity.fulfillment_channel = fulfillment_channel
                entity.search_text = search_text
                entity.updated_at = now

        # фиксируем изменения (но пока без search_tsv)
        await self._session.flush()

        # 3. одним апдейтом пересчитываем search_tsv для этого brand+marketplace
        #    тут используется конфигурация 'english'; если захочешь, можно
        #    вынести в константу или использовать 'simple'
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
