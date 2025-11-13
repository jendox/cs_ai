import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import aio_pika
import anyio
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustExchange

from src.jobs.models import JobType
from src.libs.zendesk_client.models import Brand

RETRY_DELAYS = [60, 300, 900]  # 1m, 5m, 15m (сек)
CONNECTION_TIMEOUT = 30
EXCHANGE_TIMEOUT = 10
MAX_PUBLISH_ATTEMPTS = 3
EXCHANGE = "jobs"
DLX = "jobs.dlx"


class RabbitJobQueue:
    """
    Очереди изолированы по брендам.
      main:   jobs.<job_type>.<brand>
      retryN: jobs.<job_type>.<brand>.retry.<N>
      dead:   jobs.<job_type>.<brand>.dead

    Маршрутизация:
      publish -> EXCHANGE routing_key=<job_type>.<brand>
      retry   -> DLX      routing_key=<job_type>.<brand>.retry.<attempt>
      back    -> EXCHANGE routing_key=<job_type>.<brand> (последняя retry)
      dead    -> DLX      routing_key=<job_type>.<brand>.dead
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._jobs_exchange: AbstractRobustExchange | None = None
        self._dlx_exchange: AbstractRobustExchange | None = None
        self._lock = anyio.Lock()
        self.logger = logging.getLogger("jobs.queue")

    async def setup_brand_topology(self, job_types: set[JobType], brand: Brand) -> None:
        for job_type in job_types:
            await self._declare_brand_topology(job_type, brand)

    @asynccontextmanager
    async def context(self) -> AsyncGenerator["RabbitJobQueue"]:
        try:
            yield self
        finally:
            await self.close()

    async def publish(self, job_type: JobType, message: dict, *, brand: Brand) -> bool:
        routing_key = f"{job_type.value}.{brand.value}"
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
                        "brand": brand.value,
                        "job_type": job_type.value,
                    },
                )
                await self._jobs_exchange.publish(aio_pika_message, routing_key=routing_key)
                self.logger.info(
                    "msg.publish",
                    extra={
                        "job_type": job_type.value,
                        "brand": brand.value,
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
                "brand": brand.value,
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
        brand: Brand,
        prefetch: int = 4,
    ) -> None:
        channel = await self.ensure()
        await channel.set_qos(prefetch)

        await self._declare_brand_topology(job_type, brand)

        queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}.{brand.value}",
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX,
                "x-dead-letter-routing-key": f"{job_type.value}.{brand.value}.retry.1",
            },
        )
        await queue.bind(self._jobs_exchange, routing_key=f"{job_type.value}.{brand.value}")

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(job_type, message, handler)

    # ---------- Соединение / обменники ----------

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

    # ---------- Топология (бренд-локальная) ----------

    async def _declare_brand_topology(self, job_type: JobType, brand: Brand) -> None:
        """
        Создаёт:
          - jobs.<job_type>.<brand>
          - jobs.<job_type>.<brand>.retry.<i> (DLX -> либо следующая retry, либо обратно в main)
          - jobs.<job_type>.<brand>.dead
        """
        self.logger.debug(
            "topology.declare.brand.start", extra={"job_type": job_type.value, "brand": brand.value},
        )
        channel = await self.ensure()

        # Main queue
        main_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}.{brand.value}",
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX,
                "x-dead-letter-routing-key": f"{job_type.value}.{brand.value}.retry.1",
            },
        )
        await main_queue.bind(self._jobs_exchange, routing_key=f"{job_type.value}.{brand.value}")

        # Retry queues
        for i, delay in enumerate(RETRY_DELAYS, start=1):
            next_key = (
                f"{job_type.value}.{brand.value}"
                if i == len(RETRY_DELAYS)
                else f"{job_type.value}.{brand.value}.retry.{i + 1}"
            )
            queue = await channel.declare_queue(
                name=f"jobs.{job_type.value}.{brand.value}.retry.{i}",
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": next_key,
                },
            )
            await queue.bind(self._dlx_exchange, routing_key=f"{job_type.value}.{brand.value}.retry.{i}")

        # Dead queue
        dead_queue = await channel.declare_queue(
            name=f"jobs.{job_type.value}.{brand.value}.dead",
            durable=True,
        )
        await dead_queue.bind(self._dlx_exchange, routing_key=f"{job_type.value}.{brand.value}.dead")
        self.logger.debug(
            "topology.declare.brand.completed", extra={"job_type": job_type.value, "brand": brand.value},
        )

    # ---------- Обработка сообщений ----------

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
            payload = json.loads(message.body.decode())
        except Exception:
            # нечитабельные сообщения — сразу в dead
            await message.reject(requeue=False)
            await self._send_to_dead(job_type, message)
            self.logger.error("msg.dead.unparseable", extra=extra)
            return

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
        """
        Перекидывает в бренд-локальную retry-очередь через DLX.
        Brand обязателен (кладётся в headers при publish).
        """
        await self.ensure()
        brand = headers.get("brand")
        if not brand:
            self.logger.error("msg.retry.no_brand_in_headers", extra={"job_type": job_type.value})
            brand = "unknown"

        routing_key = f"{job_type.value}.{brand}.retry.{attempt}"
        new_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers | {"attempt": attempt},
            correlation_id=message.correlation_id,
        )
        await self._dlx_exchange.publish(new_msg, routing_key=routing_key)
        await message.ack()

    async def _send_to_dead(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        headers: dict | None = None,
    ) -> None:
        """
        Кладёт сообщение в бренд-локальную dead-очередь.
        Brand обязателен (берём из headers).
        """
        await self.ensure()
        local_headers = headers or message.headers or {}
        brand = local_headers.get("brand")
        if not brand:
            self.logger.error("msg.dead.no_brand_in_headers", extra={"job_type": job_type.value})
            brand = "unknown"

        routing_key = f"{job_type.value}.{brand}.dead"
        dead_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=local_headers,
        )
        await self._dlx_exchange.publish(dead_msg, routing_key=routing_key)


async def create_job_queue(rabbitmq_url: str, brand: Brand) -> RabbitJobQueue:
    """
    Возвращает экземпляр очереди. Обменники создаются при первом ensure()/reconnect().
    Топология очередей объявляется в consume(...) под конкретный brand/job_type.
    """
    job_queue = RabbitJobQueue(rabbitmq_url)
    await job_queue.setup_brand_topology(JobType.all(), brand)
    return job_queue
