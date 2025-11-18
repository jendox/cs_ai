from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LLMRuntimeSettings as LLMSettingsEntity, LLMRuntimeSettingsKey
from src.db.repositories.base import BaseRepository


class LLMSettingsNotExists(Exception): ...


class LLMSettingsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="llm_settings_repository", session=session)

    async def get(self, key: LLMRuntimeSettingsKey) -> LLMSettingsEntity:
        stmt = (
            select(LLMSettingsEntity)
            .where(LLMSettingsEntity.key == key)
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise LLMSettingsNotExists(f"LLM settings key {key} doesn't exist.")
        return entity

    async def set(self, key: LLMRuntimeSettingsKey, value: dict[str, Any]) -> None:
        stmt = (
            pg_insert(LLMSettingsEntity)
            .values(key=key, value=value, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=[LLMSettingsEntity.key],
                set_={"value": value, "updated_at": func.now()},
            )
        )
        await self._session.execute(stmt)
