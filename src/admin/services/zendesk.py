from dataclasses import dataclass
from types import TracebackType
from typing import Self

from src.admin.services.base import BaseAdminService
from src.db.models import PostChannel
from src.db.repositories import ZendeskRuntimeSettingsRepository


@dataclass(frozen=True)
class ZendeskModeUpdateResult:
    channel: PostChannel
    changed: bool


class ZendeskAdminService(BaseAdminService):
    def __init__(self) -> None:
        super().__init__()
        self._repo: ZendeskRuntimeSettingsRepository | None = None

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._repo = ZendeskRuntimeSettingsRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await super().__aexit__(exc_type, exc, tb)
        self._repo = None

    @property
    def repo(self) -> ZendeskRuntimeSettingsRepository:
        if self._repo is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        return self._repo

    async def get_mode(self) -> PostChannel:
        async with self.session.begin():
            return await self.repo.get_channel()

    async def set_mode(self, channel: PostChannel, *, updated_by: str) -> ZendeskModeUpdateResult:
        async with self.session.begin():
            current = await self.repo.get_channel()
            if current == channel:
                return ZendeskModeUpdateResult(
                    channel=current,
                    changed=False,
                )
            await self.repo.set_channel(channel, updated_by)

        return ZendeskModeUpdateResult(
            channel=channel,
            changed=True,
        )
