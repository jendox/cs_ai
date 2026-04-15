from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, Field

from src.admin.services.base import BaseAdminService
from src.ai.config import RuntimeClassificationSettings, RuntimeResponseSettings
from src.config import LLMProvider
from src.db.models import LLMRuntimeSettingsKey
from src.db.repositories.llm_settings import LLMSettingsNotExists, LLMSettingsRepository


class ResponseSettingsPatch(BaseModel):
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    provider: LLMProvider | None = None
    model: str | None = None


class ClassificationSettingsPatch(BaseModel):
    enabled: bool | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    provider: LLMProvider | None = None
    model: str | None = None


@dataclass(frozen=True)
class LLMRuntimeSettingsView:
    response: RuntimeResponseSettings
    classification: RuntimeClassificationSettings


class LLMAdminService(BaseAdminService):
    def __init__(self) -> None:
        super().__init__()
        self._repo: LLMSettingsRepository | None = None

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._repo = LLMSettingsRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await super().__aexit__(exc_type, exc, tb)
        self._repo = None

    @property
    def repo(self) -> LLMSettingsRepository:
        if self._repo is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        return self._repo

    async def get_settings(self) -> LLMRuntimeSettingsView:
        async with self.session.begin():
            response = await self.get_response_settings()
            classification = await self.get_classification_settings()

        return LLMRuntimeSettingsView(response=response, classification=classification)

    async def get_response_settings(self) -> RuntimeResponseSettings:
        try:
            entity = await self.repo.get(LLMRuntimeSettingsKey.RESPONSE)
            return RuntimeResponseSettings.model_validate(entity.value)
        except LLMSettingsNotExists:
            settings = RuntimeResponseSettings()
            await self.repo.set(LLMRuntimeSettingsKey.RESPONSE, settings.model_dump())
            return settings

    async def get_classification_settings(self) -> RuntimeClassificationSettings:
        try:
            entity = await self.repo.get(LLMRuntimeSettingsKey.CLASSIFICATION)
            return RuntimeClassificationSettings.model_validate(entity.value)
        except LLMSettingsNotExists:
            settings = RuntimeClassificationSettings()
            await self.repo.set(LLMRuntimeSettingsKey.CLASSIFICATION, settings.model_dump())
            return settings

    async def update_response_settings(
        self,
        patch: ResponseSettingsPatch,
        *,
        updated_by: str | int | None = None,
    ) -> RuntimeResponseSettings:
        async with self.session.begin():
            current = await self.get_response_settings()
            data = patch.model_dump(exclude_none=True)

            if data.get("provider") is not None and data["provider"] != LLMProvider.GOOGLE:
                raise ValueError("Only google provider is currently supported")

            updated = current.model_copy(update=data)
            await self.repo.set(LLMRuntimeSettingsKey.RESPONSE, updated.model_dump())
            return updated

    async def update_classification_settings(
        self,
        patch: ClassificationSettingsPatch,
        *,
        updated_by: str | int | None = None,
    ) -> RuntimeClassificationSettings:
        async with self.session.begin():
            current = await self.get_classification_settings()
            data = patch.model_dump(exclude_none=True)

            if data.get("provider") is not None and data["provider"] != LLMProvider.GOOGLE:
                raise ValueError("Only google provider is currently supported")

            updated = current.model_copy(update=data)
            await self.repo.set(LLMRuntimeSettingsKey.CLASSIFICATION, updated.model_dump())
            return updated
