from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import PostChannel, ZendeskRuntimeSettings as ZendeskSettingsEntity
from src.db.repositories.base import BaseRepository


class ZendeskRuntimeSettingsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="zendesk_settings_repository", session=session)

    async def _get_row(self) -> ZendeskSettingsEntity:
        settings = await self._session.get(ZendeskSettingsEntity, "default")
        if settings is None:
            settings = ZendeskSettingsEntity(review_channel=PostChannel.INTERNAL)
            self._session.add(settings)
            await self._session.flush()
        return settings

    async def get_channel(self) -> PostChannel:
        settings = await self._get_row()
        return settings.review_channel

    async def set_channel(self, channel: PostChannel, updated_by: str) -> None:
        settings = await self._get_row()
        settings.review_channel = channel
        settings.updated_by = updated_by
        settings.updated_at = datetime_utils.utcnow()
        await self._session.flush()
