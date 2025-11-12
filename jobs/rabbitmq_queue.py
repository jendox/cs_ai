import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import aio_pika
import anyio
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustExchange

from jobs.models import JobType
from libs.zendesk_client.models import Brand

RETRY_DELAYS = [60, 300, 900]  # 1m, 5m, 15m (сек)
CONNECTION_TIMEOUT = 30
EXCHANGE_TIMEOUT = 10
MAX_PUBLISH_ATTEMPTS = 3
EXCHANGE = "jobs"
DLX = "jobs.dlx"


class RabbitJobQueue:
    def __init__(self, url: str) -> None:
        self.url = url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._jobs_exchange: AbstractRobustExchange | None = None
        self._dlx_exchange: AbstractRobustExchange | None = None
        self._lock = anyio.Lock()
        self.logger = logging.getLogger("jobs.queue")

    @asynccontextmanager
    async def context(self) -> AsyncGenerator["RabbitJobQueue"]:
        try:
            yield self
        finally:
            await self.close()

    async def setup_topology(self, job_types: set[JobType]) -> None:
        for job_type in job_types:
            await self._declare_topology(job_type)

    async def publish(self, job_type: JobType, message: dict, *, brand: Brand | None = None) -> bool:
        routing_key = f"{job_type.value}.{brand.value}" if brand else job_type.value
        body_str = json.dumps(message, ensure_ascii=False)
        body_bytes = body_str.encode()
        for attempt in range(MAX_PUBLISH_ATTEMPTS):
            try:
                await self.ensure()
                aio_pika_message = aio_pika.Message(
                    body=body_bytes,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    correlation_id=message.get("dedup_key"),
                    headers={
                        "attempt": 0,
                        "brand": brand.value if brand else None,
                        "job_type": job_type.value,
                    },
                )
                await self._jobs_exchange.publish(aio_pika_message, routing_key=routing_key)
                self.logger.info(
                    "msg.publish",
                    extra={
                        "job_type": job_type.value,
                        "brand": brand.value if brand else None,
                        "correlation_id": message.get("dedup_key"),
                    },
                )
                return True
            except (aio_pika.exceptions.AMQPError, ConnectionError) as exc:
                self.logger.warning("msg.publish.retry", extra={"attempt": attempt + 1, "error": str(exc)})
                if attempt < MAX_PUBLISH_ATTEMPTS - 1:
                    await anyio.sleep(1 * (attempt + 1))

        self.logger.error(
            "msg.publish.failed",
            extra={
                "routing_key": routing_key,
                "body": body_str,
                "job_type": job_type.value,
                "brand": brand.value if brand else None,
                "total_attempts": MAX_PUBLISH_ATTEMPTS,
                "correlation_id": message.get("dedup_key"),
            },
        )
        return False

    async def consume(
        self,
        job_type: JobType,
        handler: Callable[[dict], Awaitable[bool]],
        *,
        brand: str | None = None,
        prefetch: int = 4,
    ) -> None:
        channel = await self.ensure()
        await channel.set_qos(prefetch)
        await self._declare_topology(job_type)
        if brand:
            # отдельная очередь под бренд (опционально). Если не нужно — уберите этот блок.
            queue = await channel.declare_queue(
                name=f"jobs.{job_type.value}.{brand}",
                durable=True,
                arguments={
                    "x-dead-letter-exchange": DLX,
                    "x-dead-letter-routing-key": f"{job_type.value}.retry.1",
                },
            )
            await queue.bind(self._jobs_exchange, routing_key=f"{job_type.value}.{brand}")
        else:
            queue = await channel.get_queue(f"jobs.{job_type.value}")

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(job_type, message, handler)

    async def ensure(self) -> AbstractRobustChannel:
        async with self._lock:
            if await self._need_reconnect():
                await self._reconnect()
            return self._channel

    async def close(self) -> None:
        self._jobs_exchange = None
        self._dlx_exchange = None
        if self._channel:
            try:
                await self._channel.close()
            finally:
                self._channel = None
        if self._connection:
            try:
                await self._connection.close()
            finally:
                self._connection = None

    async def _need_reconnect(self) -> bool:
        if not self._connection or self._connection.is_closed:
            self.logger.debug("connection.reconnect_needed", extra={"reason": "no_connection_or_closed"})
            return True
        if not self._channel or self._channel.is_closed:
            self.logger.debug("connection.reconnect_needed", extra={"reason": "no_channel_or_closed"})
            return True
        try:
            await self._channel.get_exchange(EXCHANGE, ensure=False)
            return False
        except (aio_pika.exceptions.AMQPError, ConnectionError, OSError) as error:
            self.logger.debug(
                "connection.reconnect_needed", extra={"reason": "health_check_failed", "error": str(error)},
            )
            return True
        except Exception as exc:
            self.logger.debug("connection.health_check_unexpected_error", extra={"error": str(exc)})
            return True

    async def _reconnect(self):
        try:
            self.logger.debug("connection.reconnecting")
            await self.close()
            with anyio.fail_after(CONNECTION_TIMEOUT):
                self._connection = await aio_pika.connect_robust(self.url)
                self._channel = await self._connection.channel()
                await self._channel.set_qos(10)
                with anyio.fail_after(EXCHANGE_TIMEOUT):
                    self._jobs_exchange = await self._channel.declare_exchange(
                        EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True,
                    )
                    self._dlx_exchange = await self._channel.declare_exchange(
                        DLX, aio_pika.ExchangeType.TOPIC, durable=True,
                    )
            self.logger.debug("connection.reconnected")
        except Exception as exc:
            self.logger.error("connection.reconnect_failed", extra={"error": str(exc)})
            await self.close()
            raise

    async def _declare_topology(self, job_type: JobType) -> None:
        self.logger.debug("topology.declare.start", extra={"job_type": job_type.value})
        channel = await self.ensure()
        main_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}",
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX,
                "x-dead-letter-routing-key": f"{job_type.value}.retry.1",
            },
        )
        await main_queue.bind(self._jobs_exchange, routing_key=f"{job_type.value}.#")

        for i, delay in enumerate(RETRY_DELAYS, start=1):
            next_key = f"{job_type.value}" if i == len(RETRY_DELAYS) else f"{job_type.value}.retry.{i + 1}"
            queue = await channel.declare_queue(
                name=f"jobs.{job_type.value}.retry.{i}",
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": next_key,
                },
            )
            await queue.bind(self._dlx_exchange, routing_key=f"{job_type.value}.retry.{i}")

        dead_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}.dead",
            durable=True,
        )
        await dead_queue.bind(self._dlx_exchange, routing_key=f"{job_type.value}.dead")
        self.logger.debug("topology.declare.completed", extra={"job_type": job_type.value})

    async def _process_message(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        handler: Callable[[dict], Awaitable[bool]],
    ) -> None:
        extra = {"job_type": job_type.value, "correlation_id": message.correlation_id}
        self.logger.debug(
            "msg.received", extra={**extra, "attempt": int((message.headers or {}).get("attempt", 0))},
        )
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
        await self.ensure()
        # публикуем в соответствующую retry-очередь через DLX
        new_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers | {"attempt": attempt},
            correlation_id=message.correlation_id,
        )
        await self._dlx_exchange.publish(new_msg, routing_key=f"{job_type.value}.retry.{attempt}")
        await message.ack()

    async def _send_to_dead(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        headers: dict | None = None,
    ) -> None:
        await self.ensure()
        dead_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers or message.headers,
        )
        await self._dlx_exchange.publish(dead_msg, routing_key=f"{job_type.value}.dead")


async def create_job_queue(rabbitmq_url: str) -> RabbitJobQueue:
    job_queue = RabbitJobQueue(rabbitmq_url)
    await job_queue.setup_topology(JobType.all())
    return job_queue
