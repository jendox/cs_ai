import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel

from jobs.models import JobType
from libs.zendesk_client.models import Brand

RETRY_DELAYS = [60, 300, 900]  # 1m, 5m, 15m (сек)
EXCHANGE = "jobs"
DLX = "jobs.dlx"


class RabbitJobQueue:
    def __init__(self, url: str) -> None:
        self.url = url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self.logger = logging.getLogger("jobs.queue")

    async def setup_topology(self, job_types: set[JobType]) -> None:
        for job_type in job_types:
            await self._declare_topology(job_type)

    async def publish(self, job_type: JobType, message: dict, *, brand: Brand | None = None) -> None:
        channel = await self.ensure()
        routing_key = f"{job_type.value}.{brand.value}" if brand else job_type.value

        body = json.dumps(message, ensure_ascii=False).encode()
        aio_pika_message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=message.get("dedup_key"),
            headers={
                "attempt": 0,
                "brand": brand.value if brand else None,
                "job_type": job_type.value,
            },
        )
        exchange = await channel.get_exchange(EXCHANGE)
        await exchange.publish(aio_pika_message, routing_key=routing_key)
        self.logger.info(
            "msg.publish",
            extra={
                "job_type": job_type.value,
                "brand": brand.value if brand else None,
                "correlation_id": message.get("dedup_key"),
            },
        )

    async def consume(
        self,
        job_type: JobType,
        handler: Callable[[dict], Awaitable[bool]],
        *,
        brand: str | None = None,
        prefetch: int = 4,
    ) -> None:
        ch = await self.ensure()
        await ch.set_qos(prefetch)
        await self._declare_topology(job_type)
        if brand:
            # отдельная очередь под бренд (опционально). Если не нужно — уберите этот блок.
            q = await ch.declare_queue(
                name=f"jobs.{job_type.value}.{brand}",
                durable=True,
                arguments={
                    "x-dead-letter-exchange": DLX,
                    "x-dead-letter-routing-key": f"{job_type.value}.retry.1",
                },
            )
            ex = await ch.get_exchange(EXCHANGE)
            await q.bind(ex, routing_key=f"{job_type.value}.{brand}")
        else:
            q = await ch.get_queue(f"jobs.{job_type.value}")

        async with q.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(job_type, message, handler)

    async def ensure(self) -> AbstractRobustChannel:
        if not self._connection:
            self._connection = await aio_pika.connect_robust(self.url)
        if not self._channel:
            self._channel = await self._connection.channel()
            await self._channel.set_qos(10)
            await self._channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
            await self._channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
        return self._channel

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()

    async def _declare_topology(self, job_type: JobType) -> None:
        channel = await self.ensure()
        exchange = await channel.get_exchange(EXCHANGE)
        dlx = await channel.get_exchange(DLX)

        main_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}",
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX,
                "x-dead-letter-routing-key": f"{job_type.value}.retry.1",
            },
        )
        await main_queue.bind(exchange, routing_key=f"{job_type.value}.#")

        for i, delay in enumerate(RETRY_DELAYS, start=1):
            next_key = f"{job_type.value}" if i == len(RETRY_DELAYS) else f"{job_type.value}.retry.{i + 1}"
            q = await channel.declare_queue(
                name=f"jobs.{job_type.value}.retry.{i}",
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": next_key,
                },
            )
            await q.bind(dlx, routing_key=f"{job_type.value}.retry.{i}")

        dead_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}.dead",
            durable=True,
        )
        await dead_queue.bind(dlx, routing_key=f"{job_type.value}.dead")

    async def _process_message(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        handler: Callable[[dict], Awaitable[bool]],
    ) -> None:
        extra = {"job_type": job_type.value, "correlation_id": message.correlation_id}
        self.logger.debug("msg.received", extra={**extra, "attempt": int((message.headers or {}).get("attempt", 0))})
        try:
            payload = json.loads(message.body.decode("utf-8"))
        except Exception:
            # нечитабельные сообщения — сразу в dead
            await message.reject(requeue=False)
            await self._send_to_dead(job_type, message)
            self.logger.error("msg.dead.unparseable", extra=extra)
            return

        # attempts
        headers = dict(message.headers or {})
        attempt = int(headers.get("attempt", 0))

        try:
            ok = await handler(payload)  # True = обработано; False = временная ошибка
        except Exception:
            ok = False

        if ok:
            self.logger.info("msg.ack", extra=extra)
            await message.ack()
            return

        # Retry/Dead logic
        attempt += 1
        if attempt > len(RETRY_DELAYS):
            await message.reject(requeue=False)
            await self._send_to_dead(job_type, message, headers | {"attempt": attempt})
            self.logger.error("msg.dead", extra={**extra, "attempt": attempt})
        else:
            await self._nack_to_retry(job_type, attempt, message, headers)
            self.logger.warning(
                "msg.retry", extra={**extra, "attempt": attempt, "next_delay": RETRY_DELAYS[attempt - 1]},
            )

    async def _nack_to_retry(
        self,
        job_type: JobType,
        attempt: int,
        message: AbstractIncomingMessage,
        headers: dict,
    ) -> None:
        ch = await self.ensure()
        dlx = await ch.get_exchange(DLX)
        # публикуем в соответствующую retry-очередь через DLX
        new_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers | {"attempt": attempt},
            correlation_id=message.correlation_id,
        )
        await dlx.publish(new_msg, routing_key=f"{job_type.value}.retry.{attempt}")
        await message.ack()

    async def _send_to_dead(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        headers: dict | None = None,
    ) -> None:
        ch = await self.ensure()
        dlx = await ch.get_exchange(DLX)
        dead_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers or message.headers,
        )
        await dlx.publish(dead_msg, routing_key=f"{job_type.value}.dead")
