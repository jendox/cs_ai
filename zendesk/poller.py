import logging
import os
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import anyio

import datetime_utils
from db import SessionLocal
from db.repository import AcquireLockError, Repository, TicketNotFound
from jobs.models import AgentDirectiveMessage, InitialReplyMessage, JobType, TicketClosedMessage, UserReplyMessage
from jobs.rabbitmq_queue import RabbitJobQueue
from libs.zendesk_client.client import ZendeskClient
from libs.zendesk_client.models import Brand, Ticket, TicketStatus

from .models import Event, EventAuthorRole, EventKind, EventSourceType

ROBOT_TAG_RE = re.compile(r"@robot(\b|:)", re.IGNORECASE)

EVENTS_SAFETY_BACKSHIFT_MIN = 5
# запрос должен быть старше чем now-60s (400 StartTimeToRecent)
# по требованиям Zendesk Incremental Tickets (cursor)
TICKETS_SAFETY_BACKSHIFT_MIN = 5
POLL_INTERVAL_SECONDS = 90


def _get_new_checkpoint(backshift_min: int) -> datetime:
    # Возвращаем со сдвигом на 5 минут, т.к. тикеты создаются верно,
    # но сервер может не вернуть созданный тикет в 90 секунд задержки
    # опроса, и таким образом он теряется
    return datetime_utils.utcnow() - timedelta(minutes=backshift_min)


class NoStatusChange(Exception):
    """Signal that ticket status hasn't changed, so no event should be created."""

    def __init__(self, ticket_id: int, status):
        super().__init__(f"Ticket {ticket_id}: status unchanged ({status})")


class Poller:
    def __init__(self, client: ZendeskClient, brand: Brand) -> None:
        self.brand = brand
        self.client = client
        self.repo: Repository | None = None
        self.job_queue: RabbitJobQueue | None = None
        self.logger = logging.getLogger("zendesk_poller")

    async def _bootstrap_if_needed(self, cp_tickets: str, cp_events: str) -> tuple[datetime, datetime]:
        tickets_from = await self.repo.get_checkpoint(cp_tickets)
        if not tickets_from:
            tickets_from = _get_new_checkpoint(TICKETS_SAFETY_BACKSHIFT_MIN)
            await self.repo.set_checkpoint(cp_tickets, tickets_from)

        events_from = await self.repo.get_checkpoint(cp_events)
        if not events_from:
            events_from = _get_new_checkpoint(EVENTS_SAFETY_BACKSHIFT_MIN)
            await self.repo.set_checkpoint(cp_events, events_from)

        return tickets_from, events_from

    async def _process_open_tickets(self, updated_after: datetime) -> datetime:
        async for ticket in self.client.iter_tickets(updated_after, self.brand, TicketStatus.active()):
            if await self.repo.upsert_ticket_and_check_new(
                ticket=ticket,
                observing=True,
                last_seen_at=datetime_utils.utcnow(),
            ):
                await self.job_queue.publish(
                    JobType.INITIAL_REPLY,
                    message=InitialReplyMessage(
                        ticket=ticket,
                    ).model_dump(mode="json"),
                    brand=self.brand,
                )
                self.logger.debug(f"Published job: {JobType.INITIAL_REPLY.value} - ticket_id: {ticket.id}")
                updated_after = ticket.created_at
        return updated_after

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
            except TicketNotFound as error:
                self.logger.warning(error)
                continue
            except NoStatusChange as exc:
                self.logger.debug(exc)
            else:
                yield event, updated_ticket
            # Теперь получаем добавившиеся комментарии, если они есть
            async for event in self._iter_new_comments(updated_ticket.id, updated_after):
                yield event, updated_ticket

    async def _update_db_ticket(self, ticket: Ticket):
        await self.repo.upsert_ticket_and_check_new(
            Ticket(
                id=ticket.id,
                brand=self.brand,
                status=ticket.status.value,
                updated_at=ticket.updated_at,
            ),
            observing=True,
            last_seen_at=datetime_utils.utcnow(),
        )

    async def _create_status_job(self, event: Event, ticket: Ticket) -> None:
        if ticket.status in {TicketStatus.CLOSED}:
            await self.job_queue.publish(
                job_type=JobType.TICKET_CLOSED,
                message=TicketClosedMessage(
                    ticket_id=event.ticket_id,
                ).model_dump(mode="json"),
                brand=self.brand,
            )
            self.logger.debug(f"Created job: {JobType.TICKET_CLOSED} - ticket_id: {ticket.id}")

    async def _create_comment_job(self, event: Event) -> None:
        if (
            event.kind == EventKind.COMMENT_PUBLIC
            and event.author_role == EventAuthorRole.USER
        ):
            await self.job_queue.publish(
                job_type=JobType.USER_REPLY,
                message=UserReplyMessage(
                    ticket_id=event.ticket_id,
                    source_id=event.source_id,
                ).model_dump(mode="json"),
                brand=self.brand,
            )
            self.logger.debug(f"Created job: {JobType.USER_REPLY} - ticket_id: {event.ticket_id}")
        elif (
            event.kind == EventKind.COMMENT_PRIVATE
            and event.has_robot_tag
            and event.author_role == EventAuthorRole.AGENT
        ):
            await self.job_queue.publish(
                job_type=JobType.AGENT_DIRECTIVE,
                message=AgentDirectiveMessage(
                    ticket_id=event.ticket_id,
                    source_id=event.source_id,
                ).model_dump(mode="json"),
                brand=self.brand,
            )
            self.logger.debug(f"Created job: {JobType.AGENT_DIRECTIVE} - ticket_id: {event.ticket_id}")

    async def _process_events(self, updated_after: datetime) -> datetime:
        latest_seen = updated_after
        async for event, ticket in self._iter_events(updated_after):
            inserted = await self.repo.insert_event(event)
            if inserted:
                self.logger.debug(f"New event created: {event.kind} - author: {event.author_role}")
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
        tickets_from, events_from = await self._bootstrap_if_needed(cp_tickets, cp_events)
        self.logger.debug(f"Updated request time\ntickets_from: {tickets_from} - events_from: {events_from}")
        # Tickets - создаем jobs для новых тикетов
        last_seen = await self._process_open_tickets(tickets_from)
        await self.repo.set_checkpoint(cp_tickets, last_seen)
        # Events - создаем jobs для новых событий
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

    async def start(self) -> None:
        lock_name = f"poller:{self.brand.value}"
        lock_holder = f"poller:{os.getpid()}"
        cp_tickets = f"tickets_cursor:{self.brand.value}"
        cp_events = f"events_cursor:{self.brand.value}"
        self.logger.info(f"{lock_holder} is starting")
        self.job_queue = RabbitJobQueue()
        await self.job_queue.setup_topology(JobType.all())
        while True:
            try:
                await self._acquire_lock(lock_name, lock_holder)
                async with SessionLocal() as session:
                    self.repo = Repository(session)
                    async with session.begin():
                        await self._poll_once(cp_tickets, cp_events)
                        self.logger.debug("=" * 100)
            except AcquireLockError as error:
                self.logger.error(error)
            except Exception as exc:
                self.logger.warning(f"Poller iteration failed: {exc}", exc_info=True)
            finally:
                self.repo = None
                await anyio.sleep(POLL_INTERVAL_SECONDS)
