import logging
import os
import uuid
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
from logs.filters import log_ctx

from .models import Event, EventAuthorRole, EventKind, EventSourceType

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
            # Только тикеты, обновлённые после запуска — для "холодного старта"
            tickets_from = _get_new_checkpoint(TICKETS_SAFETY_BACKSHIFT_MIN)
            await self.repo.set_checkpoint(cp_tickets, tickets_from)

        events_from = await self.repo.get_checkpoint(cp_events)
        if not events_from:
            events_from = _get_new_checkpoint(EVENTS_SAFETY_BACKSHIFT_MIN)
            await self.repo.set_checkpoint(cp_events, events_from)

        return tickets_from, events_from

    async def _process_open_tickets(self, updated_after: datetime) -> datetime:
        lastest_seen = updated_after
        async for ticket in self.client.iter_tickets(updated_after, self.brand, TicketStatus.active()):
            is_new = await self.repo.upsert_ticket_and_check_new(
                ticket=ticket,
                observing=True,
                last_seen_at=datetime_utils.utcnow(),
            )
            if is_new:
                await self.job_queue.publish(
                    JobType.INITIAL_REPLY,
                    message=InitialReplyMessage(
                        ticket=ticket,
                    ).model_dump(mode="json"),
                    brand=self.brand,
                )
                self.logger.debug(
                    "job.created", extra={"ticket_id": ticket.id, "job_type": JobType.INITIAL_REPLY.value},
                )
            pivot = ticket.updated_at or ticket.created_at
            if pivot:
                lastest_seen = max(lastest_seen, pivot)
        return lastest_seen

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
            except TicketNotFound:
                self.logger.warning("ticket.not_found", extra={"ticket_id": updated_ticket.id})
                continue  # we only track tickets created after poller started
            except NoStatusChange:
                self.logger.debug("ticket.status_unchanged", extra={"ticket_id": updated_ticket.id})
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
                self.logger.debug(
                    "event.created",
                    extra={
                        "ticket_id": event.ticket_id,
                        "kind": event.kind,
                        "author": event.author_role,
                    },
                )
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
        self.logger.debug(
            "poll.window",
            extra={"tickets_from": str(tickets_from), "events_from": str(events_from)},
        )
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
                await self.repo.acquire_lock(
                    name=lock_name,
                    holder=lock_holder,
                    ttl_seconds=POLL_INTERVAL_SECONDS * 2,
                )

    async def _release_lock(self, lock_name: str, lock_holder: str) -> None:
        async with SessionLocal() as session:
            self.repo = Repository(session)
            async with session.begin():
                await self.repo.release_lock(name=lock_name, holder=lock_holder)

    async def start(self, rabbitmq_url: str) -> None:
        lock_name = f"poller:{self.brand.value}"
        lock_holder = f"poller:{os.getpid()}"
        cp_tickets = f"tickets_cursor:{self.brand.value}"
        cp_events = f"events_cursor:{self.brand.value}"

        self.logger.info("poller.start", extra={"brand": self.brand.value, "holder": lock_holder})

        self.job_queue = RabbitJobQueue(rabbitmq_url)
        await self.job_queue.setup_topology(JobType.all())
        while True:
            token = log_ctx.set({"brand": self.brand.value, "iteration_id": uuid.uuid4().hex[:8]})
            try:
                await self._acquire_lock(lock_name, lock_holder)
                async with SessionLocal() as session:
                    self.repo = Repository(session)
                    async with session.begin():
                        await self._poll_once(cp_tickets, cp_events)
                        self.logger.debug("poller.iteration_done")
            except AcquireLockError:
                self.logger.warning("lock.busy", extra={"brand": self.brand.value})
            except Exception:
                self.logger.warning("poller.iteration_failed", exc_info=True)
            finally:
                try:
                    await self._release_lock(lock_name, lock_holder)
                except Exception:
                    pass
                self.repo = None
                if self.job_queue:
                    await self.job_queue.close()
                await anyio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    log_ctx.reset(token)
                except Exception:
                    pass
