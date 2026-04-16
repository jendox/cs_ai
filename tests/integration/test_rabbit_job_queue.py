"""RabbitMQ: connect, declare topology, publish and consume one message."""

from __future__ import annotations

import json

import aio_pika
import anyio
import pytest

from src.jobs.models import JobType
from src.jobs.rabbitmq_queue import create_job_queue
from tests.integration.conftest import amqp_url

BRAND_ID = 12345


@pytest.mark.usefixtures("db_engine")
@pytest.mark.asyncio
async def test_publish_and_consume_roundtrip() -> None:
    url = amqp_url()
    job_queue = await create_job_queue(url, BRAND_ID)
    payload = {"ticket_id": 42, "probe": True}
    received: dict = {}

    async def handler(body: dict) -> bool:
        received.update(body)
        return True

    channel = await job_queue.ensure()
    main_name = f"jobs.{JobType.TICKET_CLOSED.value}.{BRAND_ID}"
    main_queue = await channel.declare_queue(name=main_name, passive=True)
    await main_queue.purge()

    async with main_queue.iterator() as queue_iter:
        publish_ok = await job_queue.publish(
            JobType.TICKET_CLOSED,
            payload,
            brand_id=BRAND_ID,
        )
        assert publish_ok is True

        with anyio.fail_after(15):
            async for message in queue_iter:
                data = json.loads(message.body.decode())
                ok = await handler(data)
                if ok:
                    await message.ack()
                break

    assert received.get("ticket_id") == 42
    await job_queue.close()


@pytest.mark.usefixtures("db_engine")
@pytest.mark.asyncio
async def test_retry_queue_declared() -> None:
    url = amqp_url()
    job_queue = await create_job_queue(url, BRAND_ID)
    try:
        channel = await job_queue.ensure()
        retry_name = f"jobs.{JobType.INITIAL_REPLY.value}.{BRAND_ID}.retry.1"
        queue = await channel.declare_queue(name=retry_name, passive=True)
        assert queue.name == retry_name
    finally:
        await job_queue.close()


@pytest.mark.usefixtures("db_engine")
@pytest.mark.asyncio
async def test_amqp_credentials() -> None:
    conn = await aio_pika.connect_robust(amqp_url(), timeout=10)
    try:
        assert not conn.is_closed
    finally:
        await conn.close()
