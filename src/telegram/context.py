from contextlib import asynccontextmanager

from src.logs.filters import log_ctx


@asynccontextmanager
async def log_context(telegram_id: int):
    token = log_ctx.set({
        "author": telegram_id,
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass
