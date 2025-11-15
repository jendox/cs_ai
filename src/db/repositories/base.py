import logging

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, name: str, session: AsyncSession) -> None:
        self._session = session
        self.logger = logging.getLogger(name)
