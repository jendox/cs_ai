from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import session_local


class BaseAdminService:
    def __init__(self) -> None:
        self._session_cm = None
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session_cm = session_local()
        self._session = await self._session_cm.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)

        self._session_cm = None
        self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(f"{type(self).__name__} must be used as an async context manager")
        return self._session
