from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import TicketsFilterRule
from src.db.repositories.base import BaseRepository
from src.tickets_filter.config import TicketsFilterRuleKind
from src.tickets_filter.dto import TicketsFilterRuleDTO

__all__ = (
    "TicketsFilterRuleRepository",
)


class TicketsFilterRuleRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="filter_rules_repository", session=session)

    async def create_rule(
        self,
        rule: TicketsFilterRuleDTO,
    ) -> TicketsFilterRule:
        entity = rule.to_entity()
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def deactivate_rule(self, rule_id: int, *, updated_by: str | None = None) -> None:
        now = datetime_utils.utcnow()
        stmt = (
            update(TicketsFilterRule)
            .where(TicketsFilterRule.id == rule_id)
            .values(
                is_active=False,
                updated_by=updated_by,
                updated_at=now,
            )
        )
        await self._session.execute(stmt)

    async def activate_rule(self, rule_id: int, *, updated_by: str | None = None) -> None:
        now = datetime_utils.utcnow()
        stmt = (
            update(TicketsFilterRule)
            .where(TicketsFilterRule.id == rule_id)
            .values(
                is_active=True,
                updated_by=updated_by,
                updated_at=now,
            )
        )
        await self._session.execute(stmt)

    async def list_rules(
        self,
        *,
        kind: TicketsFilterRuleKind | None = None,
        brand_id: int | None = None,
        via_channel: str | None = None,
        is_active: bool | None = None,
    ) -> list[TicketsFilterRuleDTO]:
        stmt = select(TicketsFilterRule)
        if kind is not None:
            stmt = stmt.where(TicketsFilterRule.kind == kind.value)
        if brand_id is not None:
            stmt = stmt.where(TicketsFilterRule.brand_id == brand_id)
        if is_active is not None:
            stmt = stmt.where(TicketsFilterRule.is_active.is_(is_active))
        if via_channel is not None:
            stmt = stmt.where(TicketsFilterRule.via_channel == via_channel)
        result = await self._session.execute(stmt)
        return [TicketsFilterRuleDTO.from_entity(rule) for rule in result.scalars().all()]
