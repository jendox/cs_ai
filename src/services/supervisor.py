import logging

import anyio

from src.services.base import Service

__all__ = (
    "supervise",
)

logger = logging.getLogger("services.supervisor")


async def supervise(service: Service, *, restart_delay: float = 5.0) -> None:
    while True:
        try:
            service.logger.info(
                "service.start", extra={"service": service.name, "brand": service.brand.value},
            )
            await service.run()
            service.logger.warning(
                "service.exit",
                extra={"service": service.name, "brand": service.brand.value},
            )
            await anyio.sleep(restart_delay)
        except anyio.get_cancelled_exc_class():
            service.logger.info(
                "service.cancelled",
                extra={"service": service.name, "brand": service.brand.value},
            )
            raise
        except Exception as exc:
            service.logger.error(
                "service.crashed",
                extra={"service": service.name, "brand": service.brand.value, "error": str(exc)},
                exc_info=True,
            )
            await anyio.sleep(restart_delay)
