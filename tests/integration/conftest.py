"""Integration tests: real Postgres + RabbitMQ (see deploy/docker-compose.test.yml)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import aio_pika
import anyio
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def postgres_url() -> str:
    return os.environ.get(
        "CS_TEST_POSTGRES_URL",
        "postgresql+asyncpg://cs_test:cs_test@127.0.0.1:55432/cs_test",
    )


def amqp_url() -> str:
    return os.environ.get(
        "CS_TEST_AMQP_URL",
        "amqp://cs_test:cs_test@127.0.0.1:55672/",
    )


async def _wait_for_postgres(url: str) -> None:
    for _ in range(60):
        try:
            engine = create_async_engine(url)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            return
        except Exception:
            await anyio.sleep(1)
    msg = f"PostgreSQL not reachable after 60s at {url}"
    raise RuntimeError(msg)


async def _wait_for_rabbit(url: str) -> None:
    for _ in range(60):
        try:
            conn = await aio_pika.connect_robust(url, timeout=5)
            await conn.close()
            return
        except Exception:
            await anyio.sleep(1)
    msg = f"RabbitMQ not reachable after 60s at {url}"
    raise RuntimeError(msg)


def _run_migrations() -> None:
    env = os.environ.copy()
    # Force test DB credentials so a developer `.env` cannot redirect Alembic.
    env.update(
        {
            "POSTGRES__USER": "cs_test",
            "POSTGRES__PASSWORD": "cs_test",
            "POSTGRES__HOST": "127.0.0.1",
            "POSTGRES__PORT": "55432",
            "POSTGRES__DB": "cs_test",
        },
    )
    venv_alembic = ROOT / ".venv" / "bin" / "alembic"
    cmd = (
        [str(venv_alembic), "upgrade", "head"]
        if venv_alembic.exists()
        else ["uv", "run", "alembic", "upgrade", "head"]
    )
    subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        check=True,
    )


@pytest.fixture(scope="session")
def integration_gate() -> None:
    anyio.run(_wait_for_postgres, postgres_url())
    anyio.run(_wait_for_rabbit, amqp_url())
    _run_migrations()
    yield


@pytest_asyncio.fixture
async def db_engine(integration_gate: None) -> None:
    from src.db.sa import Database, session_local

    url = postgres_url()
    await Database._init(url=url, echo=False)
    try:
        async with session_local() as session:
            async with session.begin():
                await session.execute(text("TRUNCATE tickets RESTART IDENTITY CASCADE"))
        yield
    finally:
        await Database._close()
