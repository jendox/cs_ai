import hashlib
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

import datetime_utils
from db.models import (
    Checkpoint as CheckpointEntity,
    Event as EventEntity,
    Job as JobEntity,
    Lock as LockEntity,
    OurPost as OurPostEntity,
    Ticket as TicketEntity,
)
from libs.zendesk_client.models import Ticket
from zendesk_poller.models import Event, Job, JobStatus


class TicketNotFound(Exception): ...


class AcquireLockError(RuntimeError):
    def __init__(self, name: str, holder: str | None):
        super().__init__(f"Lock '{name}' is held by {holder!r}")
        self.name = name
        self.holder = holder


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Tickets ---
    async def upsert_ticket(
        self,
        ticket: Ticket,
        observing: bool = True,
        last_seen_at: datetime | None = None,
    ):
        last_seen_at = last_seen_at or datetime_utils.utcnow()
        stmt = sqlite_insert(TicketEntity).values(
            ticket_id=ticket.id,
            brand_id=ticket.brand.value,
            status=ticket.status,
            updated_at=ticket.updated_at,
            observing=observing,
            last_seen_at=last_seen_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TicketEntity.ticket_id],
            set_={
                "brand_id": ticket.brand.value,
                "status": ticket.status,
                "updated_at": ticket.updated_at,
                "observing": observing,
                "last_seen_at": last_seen_at,
            },
        )

        await self._session.execute(stmt)

    async def get_ticket_status(self, ticket_id: int) -> str:
        result = await self._session.execute(
            select(TicketEntity.status)
            .where(TicketEntity.ticket_id == ticket_id),
        )
        status = result.scalar_one_or_none()
        if status is None:
            raise TicketNotFound(f"Ticket {ticket_id} doesn't exist")
        return status

    # --- Events ---
    async def insert_event(self, event: Event) -> bool:
        stmt = (
            sqlite_insert(EventEntity)
            .values(**event.model_dump())
            .prefix_with("OR IGNORE")
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    # --- Jobs ---
    async def enqueue_job(
        self,
        job: Job,
    ) -> bool:
        stmt = (
            sqlite_insert(JobEntity)
            .values(**job.model_dump()).prefix_with("OR IGNORE")
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def claim_job(self, *, visibility_timeout_seconds: int = 300) -> JobEntity | None:
        # 1) найти подходящую job_id
        now = datetime_utils.utcnow()
        result = await self._session.execute(
            select(JobEntity.job_id)
            .where(
                and_(
                    JobEntity.status == JobStatus.QUEUED,
                    (JobEntity.run_at.is_(None)) | (JobEntity.run_at <= now),
                ),
            )
            .order_by(JobEntity.created_at).limit(1),
        )
        row = result.first()
        if not row:
            return None

        job_id = row[0]
        vis_deadline = now + timedelta(seconds=visibility_timeout_seconds)

        # 2) атомарно перевести в processing (защита от гонки)
        result_2 = await self._session.execute(
            update(JobEntity)
            .where(
                and_(JobEntity.job_id == job_id, JobEntity.status == JobStatus.QUEUED),
            )
            .values(
                status=JobStatus.PROCESSING, visibility_deadline=vis_deadline, updated_at=now,
            ),
        )
        if result_2.rowcount == 0:
            return None

        # 3) вернуть полную сущность
        result_3 = await self._session.execute(
            select(JobEntity).where(JobEntity.job_id == job_id),
        )
        return result_3.scalar_one_or_none()

    async def complete_job(self, job_id: int):
        await self._session.execute(
            update(JobEntity)
            .where(JobEntity.job_id == job_id)
            .values(
                status=JobStatus.DONE,
                visibility_deadline=None,
                updated_at=datetime_utils.utcnow(),
            ),
        )

    async def fail_job(self, job_id: int, *, delay_seconds: int = 60, max_attempts: int = 5):
        now = datetime_utils.utcnow()
        result = await self._session.execute(
            select(JobEntity.attempts)
            .where(JobEntity.job_id == job_id),
        )
        attempts = (result.scalar_one_or_none() or 0) + 1
        status = JobStatus.DEAD if attempts >= max_attempts else JobStatus.QUEUED
        next_run = None
        if status == JobStatus.QUEUED:
            next_run = now + timedelta(seconds=delay_seconds * (2 ** (attempts - 1)))

        await self._session.execute(
            update(JobEntity)
            .where(JobEntity.job_id == job_id)
            .values(
                status=status,
                attempts=attempts,
                run_at=next_run,
                visibility_deadline=None,
                updated_at=now,
            ),
        )

    async def requeue_timed_out(self) -> int:
        now = datetime_utils.utcnow()
        result = await self._session.execute(
            update(JobEntity)
            .where(and_(
                JobEntity.status == JobStatus.PROCESSING,
                JobEntity.visibility_deadline.is_not(None),
                JobEntity.visibility_deadline <= now),
            )
            .values(
                status=JobStatus.QUEUED,
                visibility_deadline=None,
                updated_at=now,
            ),
        )
        return result.rowcount or 0

    # --- Our posts ---
    async def record_our_post(self, *, ticket_id: int, body_hash: str, channel: str = "private") -> bool:
        post_key = hashlib.md5(f"{ticket_id}:{body_hash}".encode()).hexdigest()
        stmt = (
            sqlite_insert(OurPostEntity)
            .values(
                post_key=post_key,
                ticket_id=ticket_id,
                body_hash=body_hash,
                channel=channel,
                created_at=datetime_utils.utcnow(),
            )
            .prefix_with("OR IGNORE"))
        res = await self._session.execute(stmt)
        return res.rowcount == 1

    # --- Checkpoints ---
    async def get_checkpoint(self, name: str) -> datetime | None:
        result = await self._session.execute(
            select(CheckpointEntity.value)
            .where(CheckpointEntity.name == name),
        )
        return result.scalar_one_or_none()

    async def set_checkpoint(self, name: str, value: datetime):
        now = datetime_utils.utcnow()
        stmt = (
            sqlite_insert(CheckpointEntity)
            .values(name=name, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=[CheckpointEntity.name],
                set_={"value": value, "updated_at": now},
            )
        )
        await self._session.execute(stmt)

    # --- Locks ---
    async def acquire_lock(
        self,
        *,
        name: str,
        holder: str,
        ttl_seconds: int = 90,
    ) -> None:
        now = datetime_utils.utcnow()
        until = now + timedelta(seconds=ttl_seconds)
        stmt = (
            sqlite_insert(LockEntity)
            .values(name=name, holder=holder, until=until)
            .on_conflict_do_update(
                index_elements=[LockEntity.name],
                set_={"holder": holder, "until": until},
                where=LockEntity.until <= now,
            )
        )
        await self._session.execute(stmt)

        result = await self._session.execute(
            select(LockEntity.holder, LockEntity.until).where(LockEntity.name == name),
        )
        current = result.scalar_one_or_none()
        if current != holder:
            raise AcquireLockError(name, current)

    async def release_lock(self, *, name: str, holder: str):
        stmt = delete(LockEntity).where(
            and_(
                LockEntity.name == name,
                LockEntity.holder == holder,
            ),
        )
        await self._session.execute(stmt)
        # await self._session.execute(text("DELETE FROM locks WHERE name=:n AND holder=:h"), {"n": name, "h": holder})
