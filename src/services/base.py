import logging
from abc import ABC, abstractmethod

from src.libs.zendesk_client.models import Brand

__all__ = (
    "Service",
)


class Service(ABC):
    def __init__(self, name: str, brand: Brand | str) -> None:
        self.name = name
        self.brand = brand
        self.logger = logging.getLogger(name)

    @abstractmethod
    async def run(self) -> None: ...
