import anyio

from src.libs.zendesk_client.models import Brand
from src.services.base import Service

__all__ = (
    "supervise",
)


async def supervise(service: Service, *, restart_delay: float = 5.0) -> None:
    brand = service.brand.value if isinstance(service.brand, Brand) else service.brand
    while True:
        try:
            service.logger.info(
                "service.start", extra={"service": service.name, "brand": brand},
            )
            await service.run()
            service.logger.warning(
                "service.exit",
                extra={"service": service.name, "brand": brand},
            )
            await anyio.sleep(restart_delay)
        except anyio.get_cancelled_exc_class():
            service.logger.info(
                "service.cancelled",
                extra={"service": service.name, "brand": brand},
            )
            raise
        except Exception as exc:
            service.logger.error(
                "service.crashed",
                extra={"service": service.name, "brand": brand, "error": str(exc)},
                exc_info=True,
            )
            await anyio.sleep(restart_delay)
