import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import aio_pika
import anyio
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustExchange,
)

from src.jobs.models import JobType
from src.libs.zendesk_client.models import Brand

RETRY_DELAYS = [60, 300, 900]  # 1m, 5m, 15m (seconds) — production values
# RETRY_DELAYS = [10, 15, 20]  # short values for testing
CONNECTION_TIMEOUT = 30
EXCHANGE_TIMEOUT = 10
MAX_PUBLISH_ATTEMPTS = 3
EXCHANGE = "jobs"
DLX = "jobs.dlx"


class RabbitJobQueue:
    """
    Brand-isolated job queues with delayed retries and dead-letter queues on RabbitMQ.

    Topology per (job_type, brand):

        main queue:   jobs.<job_type>.<brand>
        retry queue:  jobs.<job_type>.<brand>.retry.<N> (N = 1..len(RETRY_DELAYS))
        dead queue:   jobs.<job_type>.<brand>.dead

    Routing:

        publish         -> EXCHANGE ("jobs") with routing_key=<job_type>.<brand>
        schedule retry  -> DLX ("jobs.dlx") with routing_key=<job_type>.<brand>.retry.<attempt>
        retry backflow  -> from retry queue to EXCHANGE after TTL via x-dead-letter-exchange
        dead            -> DLX with routing_key=<job_type>.<brand>.dead

    Design:

    * The main queue DOES NOT have any dead-letter configuration.
      All retry/dead decisions are made in application code.
    * Retry and dead queues DO use DLX + TTL to bounce messages back to the main exchange.
    * Message processing is idempotent at the topology level: repeated declarations are safe.

    Usage pattern:

    * Producer services:
        - create an instance via `create_job_queue()`
        - call `publish()` with `job_type` and `brand`.

    * Consumer services:
        - create an instance via `create_job_queue()`
        - call `consume(job_type=..., brand=..., handler=...)` once per job type.
        - `handler(payload: dict) -> bool`:
            True  = job successfully processed; message is acked.
            False = transient failure; message is sent to retry or dead (based on attempt).
    """

    def __init__(self, url: str) -> None:
        """
        Initialize a new RabbitJobQueue instance.

        This constructor does NOT open a connection immediately. The underlying
        RabbitMQ connection and channel are created lazily on first `ensure()` /
        `publish()` / `consume()` call and are automatically re-established
        when connectivity problems are detected.

        Args:
            url: AMQP connection URL for RabbitMQ (e.g. "amqp://user:pass@host:5672/vhost").
        """
        self.url = url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._jobs_exchange: AbstractRobustExchange | None = None
        self._dlx_exchange: AbstractRobustExchange | None = None
        self._lock = anyio.Lock()
        self.logger = logging.getLogger("jobs.queue")

    # ---------- Public API ----------

    async def setup_brand_topology(self, job_types: set[JobType], brand: Brand) -> None:
        """
        Pre-declare the full queue topology for the given brand and job types.

        This method is safe to call multiple times and can be used during service
        startup to ensure that all required queues and bindings exist before any
        messages are published or consumed.

        Topology created for each (job_type, brand):

            - main queue:
                jobs.<job_type>.<brand>

            - retry queues:
                jobs.<job_type>.<brand>.retry.<i> for i in [1..len(RETRY_DELAYS)]
                each retry queue has:
                    * x-message-ttl = RETRY_DELAYS[i-1] * 1000 (ms)
                    * x-dead-letter-exchange = EXCHANGE ("jobs")
                    * x-dead-letter-routing-key = <job_type>.<brand>

            - dead queue:
                jobs.<job_type>.<brand>.dead

        Args:
            job_types: A set of job types for which to declare queues.
            brand:     Brand for which topology should be created.
        """
        for job_type in job_types:
            await self._declare_brand_topology(job_type, brand)

    @asynccontextmanager
    async def context(self) -> AsyncGenerator["RabbitJobQueue", None]:
        """
        Provide an async context manager that transparently closes the queue on exit.

        Example:
            async with job_queue.context() as queue:
                await queue.publish(...)

        The context manager simply yields `self` and ensures that `close()` is
        called at the end of the `with` block.

        Yields:
            The current RabbitJobQueue instance.
        """
        try:
            yield self
        finally:
            await self.close()

    async def publish(self, job_type: JobType, message: dict, *, brand: Brand) -> bool:
        """
        Publish a job message to the main exchange for the given job type and brand.

        The message is sent to the "jobs" exchange with routing key:

            <job_type>.<brand>

        and JSON-encoded body. Messages are marked as persistent and carry basic
        metadata in headers:

            - attempt:  0 (initial publish)
            - brand:    brand.value
            - job_type: job_type.value

        The method performs a small number of retry attempts in case of transient
        AMQP / connection errors. Each retry is delayed linearly.

        Args:
            job_type: The type of job to publish.
            message:  Payload to send; must be JSON-serializable.
            brand:    Brand to which the job belongs (used for routing).

        Returns:
            True if the message was successfully published at least once,
            False if all publish attempts failed.
        """
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
                await self._jobs_exchange.publish(
                    aio_pika_message,
                    routing_key=routing_key,
                )
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
                self.logger.warning(
                    "msg.publish.retry",
                    extra={
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "routing_key": routing_key,
                    },
                )
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
        """
        Start consuming jobs for a given job type and brand.

        This method is a long-running loop that:

            1. Ensures connection, channel and topology exist.
            2. Declares (or retrieves) the main queue:
                   jobs.<job_type>.<brand>
            3. Iterates over messages using `queue.iterator()`.
            4. For each message:
                 * parses JSON payload
                 * invokes the provided `handler(payload)`
                 * routes the message to:
                     - ack (on success)
                     - retry queue (on transient failure, respecting RETRY_DELAYS)
                     - dead queue (after exceeding retry limit, or on unparseable payload)

        Handler contract:

            async def handler(payload: dict) -> bool:
                return True   # processing succeeded; message will be acked
                return False  # transient failure; message will be retried or dead-lettered

        Args:
            job_type: Type of job to consume.
            handler:  Async callable that processes a single job payload.
            brand:    Brand to consume jobs for (isolates queues per brand).
            prefetch: Per-consumer prefetch (QoS) value; controls in-flight messages.

        Raises:
            Any exception raised from the underlying connection management
            or from aio-pika may bubble up if reconnection ultimately fails.
        """
        channel = await self.ensure()
        await channel.set_qos(prefetch)

        # Single topology declaration entry-point (idempotent).
        await self._declare_brand_topology(job_type, brand)

        # Main queue for this job type and brand.
        queue_name = f"jobs.{job_type.value}.{brand.value}"
        queue = await channel.declare_queue(
            name=queue_name,
            durable=True,
        )

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(job_type, message, handler)

    # ---------- Connection / Exchanges ----------

    async def ensure(self) -> AbstractRobustChannel:
        """
        Ensure that a healthy connection and channel exist and exchanges are declared.

        This method is safe to call from multiple coroutines; internally it uses a
        lock to prevent concurrent reconnection attempts. It performs:

            - connection existence/health check
            - channel existence/health check
            - exchange health check (EXCHANGE = "jobs")

        When any of these checks fail, `_reconnect()` is invoked to re-establish
        the connection, channel and exchanges.

        Returns:
            A live `AbstractRobustChannel` ready for use.

        Raises:
            Any exception from `_reconnect()` if reconnection ultimately fails.
        """
        async with self._lock:
            if await self._need_reconnect():
                await self._reconnect()
            assert self._channel is not None  # for type checkers/IDEs
            return self._channel

    async def close(self) -> None:
        """
        Close the underlying channel and connection, if present.

        This method is idempotent and safe to call multiple times. It:

            - clears cached exchange references
            - closes the channel (if open)
            - closes the connection (if open)

        Any exceptions raised while closing the channel/connection are not
        propagated further; the internal references are always reset to None.
        """
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
        """
        Internal health check for connection/channel/exchange.

        Returns:
            True if reconnection is required, False otherwise.
        """
        if not self._connection or self._connection.is_closed:
            self.logger.debug(
                "connection.reconnect_needed",
                extra={"reason": "no_connection_or_closed"},
            )
            return True

        if not self._channel or self._channel.is_closed:
            self.logger.debug(
                "connection.reconnect_needed",
                extra={"reason": "no_channel_or_closed"},
            )
            return True

        try:
            # Cheap health-check on the main exchange.
            await self._channel.get_exchange(EXCHANGE, ensure=False)
            return False
        except (aio_pika.exceptions.AMQPError, ConnectionError, OSError) as error:
            self.logger.debug(
                "connection.reconnect_needed",
                extra={"reason": "health_check_failed", "error": str(error)},
            )
            return True
        except Exception as exc:
            self.logger.debug(
                "connection.health_check_unexpected_error",
                extra={"error": str(exc)},
            )
            return True

    async def _reconnect(self) -> None:
        """
        Internal reconnection routine.

        Closes any existing connection/channel, then attempts to:

            - establish a new robust connection to RabbitMQ
            - create a new channel
            - set channel QoS
            - declare the main "jobs" exchange and DLX "jobs.dlx"

        Any failure during this process results in the connection being closed
        again and the exception being re-raised.

        Raises:
            Exception: if reconnection fails.
        """
        try:
            self.logger.debug("connection.reconnecting")
            await self.close()

            # Connection + channel
            with anyio.fail_after(CONNECTION_TIMEOUT):
                self._connection = await aio_pika.connect_robust(self.url)
                self._channel = await self._connection.channel()
                await self._channel.set_qos(10)

                # Exchange declarations
                with anyio.fail_after(EXCHANGE_TIMEOUT):
                    self._jobs_exchange = await self._channel.declare_exchange(
                        EXCHANGE,
                        aio_pika.ExchangeType.TOPIC,
                        durable=True,
                    )
                    self._dlx_exchange = await self._channel.declare_exchange(
                        DLX,
                        aio_pika.ExchangeType.TOPIC,
                        durable=True,
                    )

            self.logger.debug("connection.reconnected")
        except Exception as exc:
            self.logger.error(
                "connection.reconnect_failed",
                extra={"error": str(exc)},
            )
            await self.close()
            raise

    # ---------- Brand-local topology ----------

    async def _declare_brand_topology(self, job_type: JobType, brand: Brand) -> None:
        """
        Declare queues and bindings for a single (job_type, brand) pair.

        Created entities:

            - Main queue:
                name: jobs.<job_type>.<brand>
                bindings:
                    EXCHANGE "jobs" with routing_key = <job_type>.<brand>
                Notes:
                    * No DLX is configured on the main queue; retry/dead are handled in code.

            - Retry queues:
                name: jobs.<job_type>.<brand>.retry.<i> for i in [1..len(RETRY_DELAYS)]
                arguments:
                    x-message-ttl           = RETRY_DELAYS[i-1] * 1000 (milliseconds)
                    x-dead-letter-exchange  = EXCHANGE ("jobs")
                    x-dead-letter-routing-key = <job_type>.<brand>
                bindings:
                    DLX "jobs.dlx" with routing_key = <job_type>.<brand>.retry.<i>

                Behavior:
                    Messages published to DLX with routing_key=<job_type>.<brand>.retry.<i>
                    will be placed in this retry queue, held for TTL, then automatically
                    dead-lettered back to EXCHANGE "jobs" → main queue.

            - Dead queue:
                name: jobs.<job_type>.<brand>.dead
                bindings:
                    DLX "jobs.dlx" with routing_key = <job_type>.<brand>.dead

        Args:
            job_type: Job type to declare topology for.
            brand:    Brand for which topology is being declared.
        """
        job_value = job_type.value
        brand_value = brand.value

        self.logger.debug(
            "topology.declare.brand.start",
            extra={"job_type": job_value, "brand": brand_value},
        )
        channel = await self.ensure()
        assert self._jobs_exchange is not None
        assert self._dlx_exchange is not None

        # Main queue (WITHOUT DLX)
        main_queue_name = f"jobs.{job_value}.{brand_value}"
        main_queue = await channel.declare_queue(
            name=main_queue_name,
            durable=True,
        )
        await main_queue.bind(
            self._jobs_exchange,
            routing_key=f"{job_value}.{brand_value}",
        )

        # Retry queues (DLX -> EXCHANGE -> main)
        for i, delay in enumerate(RETRY_DELAYS, start=1):
            retry_queue_name = f"jobs.{job_value}.{brand_value}.retry.{i}"
            retry_queue = await channel.declare_queue(
                name=retry_queue_name,
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,  # ms
                    "x-dead-letter-exchange": EXCHANGE,
                    "x-dead-letter-routing-key": f"{job_value}.{brand_value}",
                },
            )
            await retry_queue.bind(
                self._dlx_exchange,
                routing_key=f"{job_value}.{brand_value}.retry.{i}",
            )

        # Dead queue
        dead_queue_name = f"jobs.{job_value}.{brand_value}.dead"
        dead_queue = await channel.declare_queue(
            name=dead_queue_name,
            durable=True,
        )
        await dead_queue.bind(
            self._dlx_exchange,
            routing_key=f"{job_value}.{brand_value}.dead",
        )

        self.logger.debug(
            "topology.declare.brand.completed",
            extra={"job_type": job_value, "brand": brand_value},
        )

    # ---------- Message processing ----------

    async def _process_message(
        self,
        job_type: JobType,
        message: AbstractIncomingMessage,
        handler: Callable[[dict], Awaitable[bool]],
    ) -> None:
        """
        Internal processing pipeline for a single received message.

        Steps:

            1. Extract metadata from headers (brand, attempt, correlation_id).
            2. Attempt to decode JSON payload.
                 - If decoding fails → send to dead queue and ack.
            3. Invoke user handler with decoded payload.
                 - If handler raises an exception → treated as transient failure.
            4. Based on handler result and attempt counter:
                 - ok == True:
                       * ack message.
                 - ok == False and attempt <= len(RETRY_DELAYS):
                       * increment attempt
                       * send a copy of the message to the appropriate retry queue via DLX
                       * ack the original message
                 - ok == False and attempt > len(RETRY_DELAYS):
                       * send a copy of the message to the dead queue
                       * ack the original message

        Args:
            job_type: Job type of the processed message.
            message:  Incoming AMQP message instance.
            handler:  User-provided async function that processes the payload.
        """
        headers = dict(message.headers or {})
        attempt_header = int(headers.get("attempt", 0))

        extra = {
            "job_type": job_type.value,
            "correlation_id": message.correlation_id,
            "brand": headers.get("brand"),
            "attempt": attempt_header,
        }

        self.logger.debug("msg.received", extra=extra)

        # 1. Payload parsing
        try:
            payload = json.loads(message.body.decode())
        except Exception:
            # Unparseable messages are immediately dead-lettered (no retries).
            await self._send_to_dead(job_type, message, headers | {"parse_error": True})
            await message.ack()
            self.logger.error("msg.dead.unparseable", extra=extra)
            return

        attempt = attempt_header

        # 2. Handler invocation
        try:
            ok = await handler(payload)  # True = processed; False = transient error
        except Exception as exc:
            ok = False
            self.logger.exception(
                "msg.handler.exception",
                extra={**extra, "error": str(exc)},
            )

        # 3. Success → ack
        if ok:
            self.logger.info("msg.ack", extra=extra)
            await message.ack()
            return

        # 4. Retry / Dead logic
        attempt += 1
        headers["attempt"] = attempt

        if attempt > len(RETRY_DELAYS):
            # Retry limit reached → send to dead queue and ACK original.
            await self._send_to_dead(job_type, message, headers)
            await message.ack()
            self.logger.error("msg.dead", extra={**extra, "attempt": attempt})
        else:
            # Schedule retry via DLX and ACK original.
            await self._nack_to_retry(job_type, attempt, message, headers)
            self.logger.warning(
                "msg.retry",
                extra={
                    **extra,
                    "attempt": attempt,
                    "next_delay": RETRY_DELAYS[attempt - 1],
                },
            )

    async def _nack_to_retry(
        self,
        job_type: JobType,
        attempt: int,
        message: AbstractIncomingMessage,
        headers: dict,
    ) -> None:
        """
        Send a copy of the message to the brand-local retry queue via DLX
        and acknowledge the original message.

        The retry queue is selected based on the attempt number:

            routing_key = <job_type>.<brand>.retry.<attempt>

        The message body is preserved; headers are updated to include the
        current attempt counter. Correlation ID is also preserved.

        Args:
            job_type: Job type (part of the routing key).
            attempt:  Current attempt counter (1-based).
            message:  Original incoming message.
            headers:  Headers to attach to the retried message.
        """
        await self.ensure()
        assert self._dlx_exchange is not None

        brand = headers.get("brand")
        if not brand:
            self.logger.error(
                "msg.retry.no_brand_in_headers",
                extra={"job_type": job_type.value},
            )
            brand = "unknown"

        routing_key = f"{job_type.value}.{brand}.retry.{attempt}"

        new_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
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
        Send a copy of the message to the brand-local dead-letter queue via DLX.

        The dead queue is routed via:

            routing_key = <job_type>.<brand>.dead

        If the brand is not present in headers, a fallback value "unknown"
        is used and logged as an error.

        Note:
            This method only publishes a copy of the message to the dead queue;
            it does NOT ack/reject the original message. The caller is responsible
            for acknowledging the original once dead-lettering is done.

        Args:
            job_type: Job type (part of routing key).
            message:  Original incoming message.
            headers:  Optional headers to attach. If None, original message
                      headers are used.
        """
        await self.ensure()
        assert self._dlx_exchange is not None

        local_headers = headers or dict(message.headers or {})
        brand = local_headers.get("brand")

        if not brand:
            self.logger.error(
                "msg.dead.no_brand_in_headers",
                extra={"job_type": job_type.value},
            )
            brand = "unknown"

        routing_key = f"{job_type.value}.{brand}.dead"

        dead_msg = aio_pika.Message(
            body=message.body,
            content_type=message.content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=local_headers,
            correlation_id=message.correlation_id,
        )

        await self._dlx_exchange.publish(dead_msg, routing_key=routing_key)


async def create_job_queue(rabbitmq_url: str, brand: Brand) -> RabbitJobQueue:
    """
    Factory function that creates and initializes a RabbitJobQueue instance.

    This helper performs two steps:

        1. Instantiate `RabbitJobQueue` with the provided RabbitMQ URL.
        2. Pre-declare topology for all job types for the given brand via:

               job_queue.setup_brand_topology(JobType.all(), brand)

    This is a convenient entry-point for both producers and consumers, ensuring
    all required queues/exchanges exist before normal operation.

    Args:
        rabbitmq_url: AMQP URL for RabbitMQ connection.
        brand:        Brand for which queues should be prepared.

    Returns:
        A fully initialized `RabbitJobQueue` instance.
    """
    job_queue = RabbitJobQueue(rabbitmq_url)
    await job_queue.setup_brand_topology(JobType.all(), brand)
    return job_queue
