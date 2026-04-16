from __future__ import annotations

from contextlib import asynccontextmanager

from src.brands import Brand
from src.jobs.models import JobType
from src.logs.filters import log_ctx


@asynccontextmanager
async def log_context(
    ticket_id: int,
    brand: Brand,
    iteration_id: str,
    job_type: JobType,
):
    token = log_ctx.set({
        "brand": brand.value,
        "job_type": job_type.value,
        "ticket_id": ticket_id,
        "iteration_id": iteration_id,
    })
    try:
        yield
    finally:
        try:
            log_ctx.reset(token)
        except Exception:
            pass
