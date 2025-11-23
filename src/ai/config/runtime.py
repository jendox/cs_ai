from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from src.config import LLMProvider
from src.db import session_local
from src.db.models import LLMRuntimeSettingsKey
from src.db.repositories.llm_settings import LLMSettingsNotExists, LLMSettingsRepository

__all__ = (
    "RuntimeResponseSettings",
    "RuntimeClassificationSettings",
    "LLMRuntimeSettingsStorage",
    "TSettings",
)

TSettings = TypeVar("TSettings", bound="BaseLLMRuntimeSettings")


class BaseLLMRuntimeSettings(BaseModel):
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int

    provider: LLMProvider | None = None
    model: str | None = None


class RuntimeResponseSettings(BaseLLMRuntimeSettings):
    temperature: float = Field(default=0.4, ge=0.0, le=1.0)
    max_tokens: int = Field(default=800)


class RuntimeClassificationSettings(BaseLLMRuntimeSettings):
    enabled: bool = True
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    max_tokens: int = Field(default=128)


class LLMRuntimeSettingsStorage:
    def __init__(self):
        self.logger = logging.getLogger("runtime_settings_storage")

    async def get_classification(self) -> RuntimeClassificationSettings:
        try:
            settings = await self._get(LLMRuntimeSettingsKey.CLASSIFICATION)
            return RuntimeClassificationSettings.model_validate(settings)
        except LLMSettingsNotExists:
            settings = RuntimeClassificationSettings()
            await self.set_classification(settings)
        return settings

    async def get_response(self) -> RuntimeResponseSettings:
        try:
            settings = await self._get(LLMRuntimeSettingsKey.RESPONSE)
            return RuntimeResponseSettings.model_validate(settings)
        except LLMSettingsNotExists:
            settings = RuntimeResponseSettings()
            await self.set_response(settings, "set_default")
        return settings

    async def set_classification(
        self,
        settings: RuntimeClassificationSettings,
        user_id: str | int | None = None,
    ) -> None:
        await self._set(LLMRuntimeSettingsKey.CLASSIFICATION, settings.model_dump(), user_id)

    async def set_response(
        self,
        settings: RuntimeResponseSettings,
        user_id: str | int | None = None,
    ) -> None:
        await self._set(LLMRuntimeSettingsKey.RESPONSE, settings.model_dump(), user_id)

    @staticmethod
    async def _get(key: LLMRuntimeSettingsKey) -> dict[str, Any]:
        async with session_local() as session:
            repo = LLMSettingsRepository(session)
            entity = await repo.get(key)
            return entity.value

    async def _set(
        self,
        key: LLMRuntimeSettingsKey,
        value: dict[str, Any],
        user_id: str | int | None = None,
    ) -> None:
        async with session_local() as session:
            async with session.begin():
                repo = LLMSettingsRepository(session)
                await repo.set(key, value)
                self.logger.info(
                    "set",
                    extra={
                        "key": key.value,
                        "value": json.dumps(value),
                        "user_id": user_id,
                    },
                )
