from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import TicketsFilterRule as TicketsFilterRuleEntity
from src.db.repositories.base import BaseRepository
from src.tickets_filter.dto import TicketsFilterRuleDTO

__all__ = (
    "TicketsFilterRuleNotFound",
    "TicketsFilterRuleRepository",
)


class TicketsFilterRuleNotFound(Exception): ...


class TicketsFilterRuleRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="filter_rules_repository", session=session)

    async def create_rule(
        self,
        rule: TicketsFilterRuleDTO,
    ) -> TicketsFilterRuleEntity:
        entity = rule.to_entity()
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def deactivate_rule(self, rule_id: int, *, updated_by: str | None = None) -> None:
        now = datetime_utils.utcnow()
        stmt = (
            update(TicketsFilterRuleEntity)
            .where(TicketsFilterRuleEntity.id == rule_id)
            .values(
                is_active=False,
                updated_by=updated_by,
                updated_at=now,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise TicketsFilterRuleNotFound(f"Filter rule {rule_id} not found.")

    async def activate_rule(self, rule_id: int, *, updated_by: str | None = None) -> None:
        now = datetime_utils.utcnow()
        stmt = (
            update(TicketsFilterRuleEntity)
            .where(TicketsFilterRuleEntity.id == rule_id)
            .values(
                is_active=True,
                updated_by=updated_by,
                updated_at=now,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise TicketsFilterRuleNotFound(f"Filter rule {rule_id} not found.")

    async def get_rule(self, rule_id: int) -> TicketsFilterRuleEntity:
        stmt = select(TicketsFilterRuleEntity).where(TicketsFilterRuleEntity.id == rule_id)
        rule = await self._session.scalar(stmt)
        if rule is None:
            raise TicketsFilterRuleNotFound(f"Filter rule {rule_id} not found.")
        return rule

    async def update_rule(  # noqa: PLR0913
        self,
        rule_id: int,
        *,
        kind: str,
        value: str,
        is_regex: bool,
        brand_id: int | None,
        via_channel: str | None,
        comment: str | None,
        updated_by: str | None,
    ) -> None:
        now = datetime_utils.utcnow()
        stmt = (
            update(TicketsFilterRuleEntity)
            .where(TicketsFilterRuleEntity.id == rule_id)
            .values(
                kind=kind,
                value=value,
                is_regex=is_regex,
                brand_id=brand_id,
                via_channel=via_channel,
                comment=comment,
                updated_by=updated_by,
                updated_at=now,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise TicketsFilterRuleNotFound(f"Filter rule {rule_id} not found.")

    async def list_rules(
        self,
        *,
        kind: str | None = None,
        brand_id: int | None = None,
        via_channel: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> list[TicketsFilterRuleEntity]:
        stmt = select(TicketsFilterRuleEntity)
        if kind is not None:
            stmt = stmt.where(TicketsFilterRuleEntity.kind == kind)
        if brand_id is not None:
            stmt = stmt.where(TicketsFilterRuleEntity.brand_id == brand_id)
        if is_active is not None:
            stmt = stmt.where(TicketsFilterRuleEntity.is_active.is_(is_active))
        if via_channel is not None:
            stmt = stmt.where(TicketsFilterRuleEntity.via_channel == via_channel)
        if search is not None:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    TicketsFilterRuleEntity.value.ilike(pattern),
                    TicketsFilterRuleEntity.comment.ilike(pattern),
                ),
            )
        stmt = stmt.order_by(
            TicketsFilterRuleEntity.is_active.desc(),
            TicketsFilterRuleEntity.kind.asc(),
            TicketsFilterRuleEntity.id.desc(),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
