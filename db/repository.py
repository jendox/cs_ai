import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

import datetime_utils
from db.models import (
    Checkpoint as CheckpointEntity,
    Event as EventEntity,
    Lock as LockEntity,
    OurPost as OurPostEntity,
    Ticket as TicketEntity,
)
from libs.zendesk_client.models import Ticket
from zendesk.models import Event


class TicketNotFound(Exception): ...


class AcquireLockError(RuntimeError):
    def __init__(self, name: str, holder: str | None):
        super().__init__(f"Lock '{name}' is held by {holder!r}")
        self.name = name
        self.holder = holder


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.logger = logging.getLogger("db.repository")

    # --- Tickets ---
    async def upsert_ticket_and_check_new(
        self,
        ticket: Ticket,
        observing: bool = True,
        last_seen_at: datetime | None = None,
    ) -> bool:
        """
        Upsert a ticket into the database.

        Performs an INSERT or UPDATE operation for the given ticket. If a ticket with the same ID
        already exists, it will be updated. Otherwise, a new ticket will be inserted.

        Args:
            ticket: The Ticket object to upsert
            observing: Whether the ticket is being observed (default: True)
            last_seen_at: Timestamp when the ticket was last seen. If None, uses current UTC time

        Returns:
            bool: True if the ticket was newly inserted, False if an existing ticket was updated
        """
        exists_stmt = select(TicketEntity.ticket_id).where(TicketEntity.ticket_id == ticket.id)
        exists_result = await self._session.execute(exists_stmt)
        exists = exists_result.scalar_one_or_none() is not None

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
        return not exists

    async def get_ticket_by_id(self, ticket_id: int) -> TicketEntity:
        stmt = (
            select(TicketEntity)
            .where(TicketEntity.ticket_id == ticket_id)
        )
        ticket = await self._session.scalar(stmt)
        if ticket is None:
            raise TicketNotFound(f"Ticket {ticket_id} doesn't exist.")
        return ticket

    async def get_ticket_status(self, ticket_id: int) -> str:
        ticket = await self.get_ticket_by_id(ticket_id)
        return ticket.status

    # --- Events ---
    async def insert_event(self, event: Event) -> bool:
        stmt = (
            sqlite_insert(EventEntity)
            .values(**event.model_dump())
            .on_conflict_do_nothing(index_elements=[EventEntity.event_key.key])
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

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
        self.logger.debug("checkpoint.set", extra={"name": name, "value": str(value)})

    # --- Locks ---
    async def acquire_lock(
        self,
        *,
        name: str,
        holder: str,
        ttl_seconds: int,
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
        self.logger.debug("lock.acquired", extra={"name": name, "holder": holder})

    async def release_lock(self, *, name: str, holder: str):
        stmt = delete(LockEntity).where(
            and_(
                LockEntity.name == name,
                LockEntity.holder == holder,
            ),
        )
        await self._session.execute(stmt)
        self.logger.debug("lock.released", extra={"name": name, "holder": holder})
