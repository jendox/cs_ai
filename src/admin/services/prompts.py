from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from src import datetime_utils
from src.admin.services.base import BaseAdminService
from src.ai.config.prompt import LLMPrompt, LLMPromptStorage
from src.db.models import LLMPromptKey
from src.db.repositories.prompt import LLMPromptRepository
from src.libs.zendesk_client.models import Brand


@dataclass(frozen=True)
class PromptListItem:
    brand: Brand
    key: LLMPromptKey


@dataclass(frozen=True)
class PromptUpdateResult:
    prompt: LLMPrompt
    changed: bool = True


@dataclass(frozen=True)
class PromptExport:
    prompt: LLMPrompt
    content: bytes
    filename: str


class PromptAdminService(BaseAdminService):
    def __init__(self) -> None:
        super().__init__()
        self._repo: LLMPromptRepository | None = None
        self._storage: LLMPromptStorage | None = None

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._repo = LLMPromptRepository(self.session)
        self._storage = LLMPromptStorage()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await super().__aexit__(exc_type, exc, tb)
        self._repo = None
        self._storage = None

    @property
    def repo(self) -> LLMPromptRepository:
        if self._repo is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        return self._repo

    @property
    def storage(self) -> LLMPromptStorage:
        if self._storage is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        return self._storage

    def list_prompt_keys(self) -> list[PromptListItem]:
        return [
            PromptListItem(brand=brand, key=key)
            for brand in Brand.supported()
            for key in LLMPromptKey
        ]

    async def get_prompt(self, brand: Brand, key: LLMPromptKey) -> LLMPrompt:
        async with self.session.begin():
            return await self._load_prompt(brand, key)

    async def update_prompt(
        self,
        *,
        brand: Brand,
        key: LLMPromptKey,
        text: str,
        updated_by: str,
        comment: str | None = None,
    ) -> PromptUpdateResult:
        async with self.session.begin():
            await self.repo.set(
                key=key,
                brand_id=brand.value,
                text=text,
                updated_by=updated_by,
                comment=comment,
            )
            entity = await self.repo.get(key=key, brand_id=brand.value)
            prompt = LLMPrompt.from_entity(entity)

        return PromptUpdateResult(prompt=prompt)

    async def _load_prompt(self, brand: Brand, key: LLMPromptKey) -> LLMPrompt:
        if key == LLMPromptKey.INITIAL_REPLY:
            return await self.storage.initial_reply_prompt(brand)
        if key == LLMPromptKey.FOLLOWUP_REPLY:
            return await self.storage.followup_reply_prompt(brand)
        if key == LLMPromptKey.CLASSIFICATION:
            return await self.storage.classification_prompt(brand)

        raise ValueError(f"Unsupported LLMPromptKey: {key}")

    async def export_prompt(self, brand: Brand, key: LLMPromptKey) -> PromptExport:
        async with self.session.begin():
            prompt = await self._load_prompt(brand, key)

        content = (prompt.text or "").encode()
        timestamp = datetime_utils.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{brand.name.lower()}_{key.value}_{timestamp}.txt"

        return PromptExport(prompt=prompt, content=content, filename=filename)

    async def import_prompt(
        self,
        *,
        brand: Brand,
        key: LLMPromptKey,
        text: str,
        updated_by: str,
        comment: str | None = None,
    ) -> PromptUpdateResult:
        return await self.update_prompt(
            brand=brand, key=key, text=text, updated_by=updated_by, comment=comment,
        )
