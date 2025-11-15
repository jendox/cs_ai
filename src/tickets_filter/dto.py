from dataclasses import dataclass
from typing import Self

from src import datetime_utils
from src.db.models import TicketsFilterRule as TicketsFilterRuleEntity

from .config import TicketsFilterRuleKind

__all__ = (
    "TicketsFilterRuleDTO",
)


@dataclass(frozen=True)
class TicketsFilterRuleDTO:
    """
    DTO for filter rules, independent from the database layer.

    The repository must return a list of these DTO instances,
    and the filter logic must operate only on this DTO, not on ORM models.
    """
    kind: TicketsFilterRuleKind
    value: str
    id: int | None = None
    is_regex: bool = False
    brand_id: int | None = None
    via_channel: str | None = None
    comment: str | None = None
    created_by: str | None = None
    is_active: bool = True

    @classmethod
    def from_entity(cls, rule: TicketsFilterRuleEntity) -> Self:
        return cls(
            id=rule.id,
            kind=TicketsFilterRuleKind(rule.kind),
            value=rule.value,
            is_regex=rule.is_regex,
            brand_id=rule.brand_id,
            via_channel=rule.via_channel,
            comment=rule.comment,
            created_by=rule.created_by,
            is_active=rule.is_active,
        )

    def to_entity(self) -> TicketsFilterRuleEntity:
        now = datetime_utils.utcnow()
        return TicketsFilterRuleEntity(
            kind=self.kind.value,
            value=self.value,
            brand_id=self.brand_id,
            is_regex=self.is_regex,
            via_channel=self.via_channel,
            comment=self.comment,
            created_by=self.created_by,
            updated_by=self.created_by,
            created_at=now,
            updated_at=now,
        )
