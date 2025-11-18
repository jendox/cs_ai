from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LLMPrompt as LLMPromptEntity, LLMPromptKey
from src.db.repositories.base import BaseRepository


class LLMPromptNotExists(Exception): ...


class LLMPromptRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="llm_prompt_repository", session=session)

    async def get(self, key: LLMPromptKey, brand_id: int) -> LLMPromptEntity:
        stmt = (
            select(LLMPromptEntity)
            .where(
                LLMPromptEntity.key == key,
                LLMPromptEntity.brand_id == brand_id,
            )
        )
        entity = await self._session.scalar(stmt)
        if entity is None:
            raise LLMPromptNotExists(f"LLM prompt key {key} doesn't exist.")
        return entity

    async def set(
        self,
        key: LLMPromptKey,
        brand_id: int,
        text: str,
        updated_by: str,
        comment: str | None = None,
    ) -> None:
        stmt = (
            pg_insert(LLMPromptEntity)
            .values(
                key=key,
                brand_id=brand_id,
                text=text,
                updated_by=updated_by,
                updated_at=func.now(),
                comment=comment,
            )
            .on_conflict_do_update(
                index_elements=[LLMPromptEntity.key, LLMPromptEntity.brand_id],
                set_={
                    "text": text,
                    "updated_by": updated_by,
                    "updated_at": func.now(),
                    "comment": comment,
                },
            )
        )
        await self._session.execute(stmt)
