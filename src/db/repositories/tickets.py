from datetime import datetime

from sqlalchemy import case, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db.models import Ticket as TicketEntity
from src.db.repositories.base import BaseRepository
from src.libs.zendesk_client.models import Ticket

__all__ = (
    "TicketNotFound",
    "TicketsRepository",
)


class TicketNotFound(Exception): ...


class TicketsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(name="tickets_repository", session=session)

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

        observing := CASE WHEN tickets.observing = FALSE THEN FALSE ELSE EXCLUDED.observing END
        !!! Important: This method never change observing from False to True !!!

        Args:
            ticket: The Ticket object to upsert
            observing: Whether the ticket is being observed (default: True)
            last_seen_at: Timestamp when the ticket was last seen. If None, uses current UTC time

        Returns:
            bool: True if the ticket was newly inserted, False if an existing ticket was updated
        """
        last_seen_at = last_seen_at or datetime_utils.utcnow()
        existed = await self._session.scalar(
            select(TicketEntity.ticket_id).where(TicketEntity.ticket_id == ticket.id),
        )

        stmt = pg_insert(TicketEntity).values(
            ticket_id=ticket.id,
            brand_id=ticket.brand.value,
            status=ticket.status.value,
            updated_at=ticket.updated_at,
            observing=observing,
            last_seen_at=last_seen_at,
        )
        # - observing: сохраняем False, если он уже False; иначе берём входящее значение (EXCLUDED.observing)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TicketEntity.ticket_id],
            set_={
                "brand_id": ticket.brand.value,
                "status": ticket.status.value,
                "updated_at": ticket.updated_at,
                "last_seen_at": last_seen_at,
                "observing": case(
                    (TicketEntity.observing == literal(False), literal(False)),
                    else_=stmt.excluded.observing,
                ),
            },
        )
        await self._session.execute(stmt)
        return existed is None

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
