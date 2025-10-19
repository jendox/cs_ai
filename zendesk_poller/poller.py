import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import anyio

import datetime_utils
from db import SessionLocal
from db.repository import AcquireLockError, Repository, TicketNotFound
from libs.zendesk_client.client import ZendeskClient
from libs.zendesk_client.models import Brand, Ticket, TicketStatus
from zendesk_poller.models import Event, EventAuthorRole, EventKind, EventSourceType, Job, JobType

ROBOT_TAG_RE = re.compile(r"@robot(\b|:)", re.IGNORECASE)

EVENTS_SAFETY_BACKSHIFT_MIN = 5
# запрос должен быть старше чем now-60s (400 StartTimeToRecent)
# по требованиям Zendesk Incremental Tickets (cursor)
BOOTSTRAP_WINDOW_MINUTES = 1
POLL_INTERVAL_SECONDS = 90


class NoStatusChange(Exception):
    """Signal that ticket status hasn't changed, so no event should be created."""

    def __init__(self, ticket_id: int, status):
        super().__init__(f"Ticket {ticket_id}: status unchanged ({status})")


class Poller:
    def __init__(self, client: ZendeskClient, brand: Brand) -> None:
        self.brand = brand
        self.client = client
        self.repo: Repository | None = None
        self.logger = logging.getLogger("zendesk_poller")

    async def _bootstrap_if_needed(self, cp_tickets: str, cp_events: str) -> tuple[datetime, datetime]:
        tickets_from = await self.repo.get_checkpoint(cp_tickets)
        now = datetime_utils.utcnow()
        if not tickets_from:
            tickets_from = now - timedelta(minutes=BOOTSTRAP_WINDOW_MINUTES * 60 * 24)
            await self.repo.set_checkpoint(cp_tickets, tickets_from)

        events_from = await self.repo.get_checkpoint(cp_events)
        if not events_from:
            events_from = now - timedelta(minutes=EVENTS_SAFETY_BACKSHIFT_MIN)
            await self.repo.set_checkpoint(cp_events, events_from)

        return tickets_from, events_from

    async def _process_open_tickets(self, updated_after: datetime) -> None:
        async for ticket in self.client.iter_tickets(updated_after, self.brand, TicketStatus.active()):
            await self.repo.upsert_ticket(ticket=ticket, observing=True, last_seen_at=datetime_utils.utcnow())
            job = Job(ticket_id=ticket.id, payload_json=ticket.to_json_str())
            await self.repo.enqueue_job(job)

    async def _create_status_event(self, ticket: Ticket) -> Event:
        prev_status = await self.repo.get_ticket_status(ticket.id)
        if ticket.status != TicketStatus(prev_status):
            return Event(
                ticket_id=ticket.id,
                source_type=EventSourceType.STATUS,
                source_id=ticket.updated_at,
                kind=EventKind.STATUS_CHANGE,
                created_at=ticket.updated_at,
                inserted_at=datetime_utils.utcnow(),
            )
        raise NoStatusChange(ticket_id=ticket.id, status=ticket.status)

    async def _iter_new_comments(self, ticket_id: int, updated_after: datetime) -> AsyncGenerator[Event, None]:
        comments = await self.client.get_ticket_comments(ticket_id)
        for comment in comments:
            if not comment.created_at or comment.created_at <= updated_after:
                continue
            yield Event(
                ticket_id=ticket_id,
                source_type=EventSourceType.COMMENT,
                source_id=str(comment.id),
                kind=EventKind.COMMENT_PUBLIC if comment.public else EventKind.COMMENT_PRIVATE,
                author_id=comment.author_id,
                is_private=False if comment.public else True,
                body=comment.body,
                created_at=comment.created_at,
                inserted_at=datetime_utils.utcnow(),
            )

    async def _iter_events(self, updated_after: datetime) -> AsyncGenerator[tuple[Event, Ticket], None]:
        async for updated_ticket in self.client.iter_tickets(
            updated_after=updated_after,
            brand=self.brand,
            statuses=TicketStatus.all(),
        ):
            try:
                # Сначала проходимся по измененным статусам
                event = await self._create_status_event(updated_ticket)
                yield event, updated_ticket
                # Теперь получаем добавившиеся комментарии, если они есть
                async for event in self._iter_new_comments(updated_ticket.id, updated_after):
                    yield event, updated_ticket

            except NoStatusChange as exc:
                self.logger.info(exc)
            except TicketNotFound as error:
                self.logger.warning(error)
                continue

    async def _update_db_ticket(self, ticket: Ticket):
        await self.repo.upsert_ticket(
            Ticket(
                id=ticket.id,
                brand=self.brand,
                status=ticket.status,
                updated_at=ticket.updated_at,
            ),
            observing=True,
            last_seen_at=datetime_utils.utcnow(),
        )

    async def _create_status_job(self, event: Event, ticket: Ticket) -> None:
        if ticket.status in {TicketStatus.SOLVED, TicketStatus.CLOSED}:
            await self.repo.enqueue_job(Job(
                ticket_id=event.ticket_id,
                job_type=JobType.TICKET_CLOSED,
                payload_json=json.dumps({"at": event.created_at.timestamp()}),
            ))

    async def _create_comment_job(self, event: Event) -> None:
        if (
            event.kind == EventKind.COMMENT_PUBLIC
            and event.author_role == EventAuthorRole.USER
        ):
            await self.repo.enqueue_job(Job(
                ticket_id=event.ticket_id,
                job_type=JobType.USER_REPLY,
                payload_json=json.dumps({"source_id": event.source_id}),
            ))
        elif (
            event.kind == EventKind.COMMENT_PRIVATE
            and event.has_robot_tag
            and event.author_role == EventAuthorRole.AGENT
        ):
            await self.repo.enqueue_job(Job(
                ticket_id=event.ticket_id,
                job_type=JobType.AGENT_DIRECTIVE,
                payload_json=json.dumps({"source_id": event.source_id}),
            ))

    async def _process_events(self, updated_after: datetime) -> datetime:
        latest_seen = updated_after
        async for event, ticket in self._iter_events(updated_after):
            inserted = await self.repo.insert_event(event)
            if inserted:
                # Изменился статус
                if event.source_type == EventSourceType.STATUS:
                    await self._create_status_job(event, ticket)
                # Добавили комментарий
                elif event.source_type == EventSourceType.COMMENT:
                    await self._create_comment_job(event)
            # Параллельно — обновляем краткое состояние тикета в БД
            await self._update_db_ticket(ticket)

            latest_seen = max(event.created_at, latest_seen)
        return latest_seen

    async def _poll_once(self, cp_tickets: str, cp_events: str):
        moved = await self.repo.requeue_timed_out()
        self.logger.info("Requeued %d timed-out jobs", moved)
        tickets_from, events_from = await self._bootstrap_if_needed(cp_tickets, cp_events)
        # Tickets - создаем jobs для новых тикетов
        await self._process_open_tickets(tickets_from)
        await self.repo.set_checkpoint(cp_tickets, datetime_utils.utcnow())
        # TODO: добавить небольшую задержку в 5 секунд для прода
        # await anyio.sleep(5.0)
        # Events
        latest_seen = await self._process_events(events_from)
        await self.repo.set_checkpoint(cp_events, latest_seen)

    async def _acquire_lock(self, lock_name: str, lock_holder: str) -> None:
        async with SessionLocal() as session:
            self.repo = Repository(session)
            async with session.begin():
                await self.repo.acquire_lock(name=lock_name, holder=lock_holder)

    async def _release_lock(self, lock_name: str, lock_holder: str) -> None:
        async with SessionLocal() as session:
            self.repo = Repository(session)
            async with session.begin():
                await self.repo.release_lock(name=lock_name, holder=lock_holder)

    async def start_polling(self) -> None:
        lock_name = f"poller:{self.brand.value}"
        lock_holder = f"poller-{os.getpid()}"
        cp_tickets = f"tickets_bootstrap:{self.brand.value}"
        cp_events = f"events_cursor:{self.brand.value}"
        while True:
            try:
                await self._acquire_lock(lock_name, lock_holder)
                async with SessionLocal() as session:
                    self.repo = Repository(session)
                    async with session.begin():
                        await self._poll_once(cp_tickets, cp_events)
            except AcquireLockError as error:
                self.logger.error(error)
            except Exception as exc:
                self.logger.warning("Poller iteration failed: %s", exc, exc_info=True)
            finally:
                await anyio.sleep(POLL_INTERVAL_SECONDS)
