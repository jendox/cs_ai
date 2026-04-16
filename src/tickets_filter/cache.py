import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import CheckpointsRepository, TicketsFilterRuleRepository
from src.tickets_filter.config import FilterConfig
from src.tickets_filter.dto import TicketsFilterRuleDTO
from src.tickets_filter.filter import TicketsFilter

__all__ = (
    "TicketsFilterCache",
    "tickets_filter_cache",
    "get_checkpoint_name",
)

CHECKPOINT_NAME_TEMPLATE = "tickets_filter_rules:{brand_id}"


def get_checkpoint_name(brand_id: int) -> str:
    return CHECKPOINT_NAME_TEMPLATE.format(brand_id=brand_id)


@dataclass
class _CacheEntry:
    version: datetime | None
    filter_: TicketsFilter


class TicketsFilterCache:
    def __init__(self):
        self._by_brand: dict[int, _CacheEntry] = {}
        self.logger = logging.getLogger("tickets_filter.cache")

    async def get_filter(self, session: AsyncSession, brand_id: int) -> TicketsFilter:
        checkpoints_repo = CheckpointsRepository(session)
        rules_repo = TicketsFilterRuleRepository(session)

        checkpoint_name = get_checkpoint_name(brand_id)
        checkpoint_value = await checkpoints_repo.get_checkpoint(checkpoint_name)
        cached = self._by_brand.get(brand_id)

        if cached is not None and cached.version == checkpoint_value:
            return cached.filter_

        rules = [
            rule
            for rule in await rules_repo.list_rules(is_active=True)
            if rule.brand_id is None or rule.brand_id == brand_id
        ]
        config = FilterConfig.from_rules([TicketsFilterRuleDTO.from_entity(rule) for rule in rules])
        filter_ = TicketsFilter(config)

        self._by_brand[brand_id] = _CacheEntry(
            version=checkpoint_value,
            filter_=filter_,
        )
        self.logger.info(
            "reload",
            extra={
                "brand_id": brand_id,
                "rules_count": len(rules),
                "checkpoint": checkpoint_value,
            },
        )
        return filter_

    def clear(self) -> None:
        self._by_brand.clear()


tickets_filter_cache = TicketsFilterCache()
