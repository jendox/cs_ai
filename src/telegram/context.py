from contextlib import asynccontextmanager

from src.logs.filters import log_ctx


@asynccontextmanager
async def log_context(ticket_id: int, telegram_id: int):
    token = log_ctx.set({
        "ticket_id": ticket_id,
        "telegram_id": telegram_id,
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass
