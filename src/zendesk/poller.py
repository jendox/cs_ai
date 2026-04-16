import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from src import datetime_utils
from src.db import session_local
from src.db.repositories import (
    AcquireLockError,
    CheckpointsRepository,
    EventsRepository,
    LocksRepository,
    TicketNotFound,
    TicketsRepository,
)
from src.jobs.models import AgentDirectiveMessage, InitialReplyMessage, JobType, TicketClosedMessage, UserReplyMessage
from src.jobs.rabbitmq_queue import RabbitJobQueue, create_job_queue
from src.libs.zendesk_client.client import ZendeskClient
from src.libs.zendesk_client.models import Brand, Comment, Ticket, TicketStatus
from src.logs.filters import log_ctx
from src.services import Service
from src.services.ticket_attachments import store_ticket_comment_attachments

from .models import Event, EventAuthorRole, EventKind, EventSourceType, comment_to_event

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


class Poller(Service):
    def __init__(
        self,
        zendesk_client: ZendeskClient,
        amqp_url: str,
        brand: Brand,
    ) -> None:
        super().__init__(name="zendesk_poller", brand=brand)
        self._zendesk_client = zendesk_client
        self._amqp_url = amqp_url
        self.job_queue: RabbitJobQueue | None = None

    @staticmethod
    async def _upsert_ticket(ticket: Ticket, observing: bool = True) -> bool:
        """
        Сохраняет тикет в БД и возвращает, был ли он новым.

        ВАЖНО: этот апдейт выполняется в ОТДЕЛЬНОЙ транзакции, НЕ в общей
        транзакции poller-итерации.

        Причина — гонка с воркером:
          1) Поллер обнаруживает новый тикет и публикует задачу в RabbitMQ.
          2) Воркер почти сразу берёт задачу и пытается вставить/обновить
             связанные записи (our_posts и т.п.), у которых есть FK на tickets.ticket_id.
          3) Если бы запись о тикете находилась в той же транзакции, что и логика
             poller-итерации (session.begin() в _run_polling_cycle), воркер мог бы
             увидеть задачу РАНЬШЕ, чем коммит транзакции, и словить ForeignKeyViolation.

        Поэтому:
          * _upsert_ticket использует отдельную сессию + отдельную транзакцию,
            которая гарантированно коммитится ДО публикации задач.
          * После возврата из _upsert_ticket запись о тикете уже есть в БД, и воркер,
            прочитав задачу из очереди, не упадёт на FK.

        Именно из-за этого инварианта _upsert_ticket НЕ интегрирован в общую
        транзакцию _run_polling_cycle и не использует переданный извне session.
        """
        async with session_local() as session:
            repo = TicketsRepository(session)
            async with session.begin():
                is_new = await repo.upsert_ticket_and_check_new(
                    ticket=ticket,
                    observing=observing,
                )
        return is_new

    @staticmethod
    async def _bootstrap_if_needed(
        checkpoints_repo: CheckpointsRepository,
        cp_tickets: str,
        cp_events: str,
    ) -> tuple[datetime, datetime]:
        tickets_from = await checkpoints_repo.get_checkpoint(cp_tickets)
        if not tickets_from:
            # Только тикеты, обновлённые после запуска — для "холодного старта"
            tickets_from = _get_new_checkpoint(TICKETS_SAFETY_BACKSHIFT_MIN)
            await checkpoints_repo.set_checkpoint(cp_tickets, tickets_from)

        events_from = await checkpoints_repo.get_checkpoint(cp_events)
        if not events_from:
            events_from = _get_new_checkpoint(EVENTS_SAFETY_BACKSHIFT_MIN)
            await checkpoints_repo.set_checkpoint(cp_events, events_from)

        return tickets_from, events_from

    async def _process_open_tickets(self, updated_after: datetime) -> tuple[datetime, set[int]]:
        lastest_seen = updated_after
        initial_reply_ticket_ids: set[int] = set()
        async for ticket in self._zendesk_client.iter_updated_tickets(updated_after, self.brand, TicketStatus.active()):
            is_new = await self._upsert_ticket(ticket=ticket, observing=True)
            if is_new:
                initial_reply_ticket_ids.add(ticket.id)
                await self.job_queue.publish(
                    JobType.INITIAL_REPLY,
                    message=InitialReplyMessage(
                        ticket=ticket,
                    ).model_dump(mode="json"),
                    brand=self.brand,
                )
            pivot = ticket.updated_at or ticket.created_at
            if pivot:
                lastest_seen = max(lastest_seen, pivot)
        return lastest_seen, initial_reply_ticket_ids

    @staticmethod
    async def _create_status_event(prev_status: TicketStatus, ticket: Ticket) -> Event:
        if ticket.status != prev_status:
            return Event(
                ticket_id=ticket.id,
                source_type=EventSourceType.STATUS,
                source_id=ticket.updated_at,
                kind=EventKind.STATUS_CHANGE,
                created_at=ticket.updated_at,
                inserted_at=datetime_utils.utcnow(),
            )
        raise NoStatusChange(ticket_id=ticket.id, status=ticket.status)

    async def _iter_new_comments(
        self,
        ticket_id: int,
        updated_after: datetime,
    ) -> AsyncGenerator[tuple[Event, Comment], None]:
        comments = await self._zendesk_client.get_ticket_comments(ticket_id)
        for comment in comments:
            if not comment.created_at or comment.created_at <= updated_after:
                continue
            yield comment_to_event(ticket_id, comment), comment

    async def _iter_events(
        self,
        tickets_repo: TicketsRepository,
        updated_after: datetime,
    ) -> AsyncGenerator[tuple[Event, Ticket, Comment | None], None]:
        async for updated_ticket in self._zendesk_client.iter_updated_tickets(
            updated_after=updated_after,
            brand=self.brand,
            statuses=TicketStatus.all(),
        ):
            ticket_id = updated_ticket.id
            try:
                # Получаем observing тикета. Если False, тикет исключен из наблюдения, пропускаем
                ticket_entity = await tickets_repo.get_ticket_by_id(ticket_id)
                if not ticket_entity.observing:
                    await self._update_db_ticket(updated_ticket)
                    continue
                # Сначала проходимся по измененным статусам
                event = await self._create_status_event(TicketStatus(ticket_entity.status), updated_ticket)
            except TicketNotFound:
                self.logger.warning("ticket.not_found", extra={"ticket_id": ticket_id})
                continue  # we only track tickets created after poller started
            except NoStatusChange:
                self.logger.debug("ticket.status_unchanged", extra={"ticket_id": ticket_id})
            else:
                yield event, updated_ticket, None
            # Теперь получаем добавившиеся комментарии, если они есть
            async for event, comment in self._iter_new_comments(ticket_id, updated_after):
                yield event, updated_ticket, comment

    async def _update_db_ticket(self, ticket: Ticket):
        update_ticket = Ticket(
            id=ticket.id,
            brand=self.brand,
            status=ticket.status.value,
            updated_at=ticket.updated_at,
        )
        await self._upsert_ticket(ticket=update_ticket, observing=True)

    async def _create_status_job(self, event: Event, ticket: Ticket) -> None:
        if ticket.status in {TicketStatus.SOLVED, TicketStatus.CLOSED}:
            await self.job_queue.publish(
                job_type=JobType.TICKET_CLOSED,
                message=TicketClosedMessage(
                    ticket_id=event.ticket_id,
                ).model_dump(mode="json"),
                brand=self.brand,
            )

    async def _create_comment_job(self, event: Event) -> None:
        if (
            event.kind == EventKind.COMMENT_PUBLIC
            and event.author_role == EventAuthorRole.USER
        ):
            await self.job_queue.publish(
                job_type=JobType.FOLLOWUP_REPLY,
                message=UserReplyMessage(
                    ticket_id=event.ticket_id,
                    source_id=event.source_id,
                ).model_dump(mode="json"),
                brand=self.brand,
            )
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

    async def _store_comment_attachments(
        self,
        session: AsyncSession,
        event: Event,
        comment: Comment | None,
    ) -> None:
        if comment is None:
            return

        attachments_count = await store_ticket_comment_attachments(
            session,
            ticket_id=event.ticket_id,
            comments=[comment],
        )
        if attachments_count:
            self.logger.info(
                "comment_attachments.stored",
                extra={"count": attachments_count},
            )

    @staticmethod
    def _should_skip_initial_followup(
        event: Event,
        initial_reply_ticket_ids: set[int],
    ) -> bool:
        return (
            event.ticket_id in initial_reply_ticket_ids
            and event.kind == EventKind.COMMENT_PUBLIC
            and event.author_role == EventAuthorRole.USER
        )

    async def _process_events(
        self,
        session: AsyncSession,
        tickets_repo: TicketsRepository,
        events_repo: EventsRepository,
        updated_after: datetime,
        initial_reply_ticket_ids: set[int],
    ) -> datetime:
        latest_seen = updated_after
        async for event, ticket, comment in self._iter_events(tickets_repo, updated_after):
            inserted = await events_repo.insert_event(event)
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
                    await self._store_comment_attachments(session, event, comment)
                    if self._should_skip_initial_followup(event, initial_reply_ticket_ids):
                        self.logger.info("followup.skipped_initial_comment")
                    else:
                        await self._create_comment_job(event)
            # Параллельно — обновляем краткое состояние тикета в БД
            await self._update_db_ticket(ticket)

            latest_seen = max(event.created_at, latest_seen)
        return latest_seen

    async def _poll_once(
        self,
        session: AsyncSession,
        tickets_repo: TicketsRepository,
        events_repo: EventsRepository,
        checkpoints_repo: CheckpointsRepository,
        cp_tickets: str,
        cp_events: str,
    ) -> None:
        tickets_from, events_from = await self._bootstrap_if_needed(checkpoints_repo, cp_tickets, cp_events)
        self.logger.debug(
            "poll.window",
            extra={"tickets_from": str(tickets_from), "events_from": str(events_from)},
        )
        # Tickets - создаем jobs для новых тикетов
        last_seen, initial_reply_ticket_ids = await self._process_open_tickets(tickets_from)
        await checkpoints_repo.set_checkpoint(cp_tickets, last_seen)
        # Events - создаем jobs для новых событий
        latest_seen = await self._process_events(
            session,
            tickets_repo,
            events_repo,
            events_from,
            initial_reply_ticket_ids,
        )
        await checkpoints_repo.set_checkpoint(cp_events, latest_seen)

    async def run(self) -> None:
        self.job_queue = await create_job_queue(self._amqp_url, self.brand)
        while True:
            async with self._polling_iteration_context():
                if await self._try_acquire_lock():
                    await self._run_polling_cycle()

    async def _acquire_lock(self) -> None:
        async with session_local() as session:
            repo = LocksRepository(session)
            async with session.begin():
                await repo.acquire_lock(
                    name=f"poller:{self.brand.value}",
                    holder=f"poller:{os.getpid()}",
                    ttl_seconds=POLL_INTERVAL_SECONDS * 2,
                )
        self.logger.debug("lock.acquired", extra={"brand": self.brand.value})

    async def _release_lock(self) -> None:
        async with session_local() as session:
            repo = LocksRepository(session)
            async with session.begin():
                await repo.release_lock(
                    name=f"poller:{self.brand.value}",
                    holder=f"poller:{os.getpid()}",
                )
        self.logger.debug("lock.released", extra={"brand": self.brand.value})

    async def _try_acquire_lock(self) -> bool:
        try:
            await self._acquire_lock()
            return True
        except AcquireLockError:
            self.logger.warning("lock.busy", extra={"brand": self.brand.value})
            return False

    async def _cleanup_iteration(self, token) -> None:
        try:
            await self._release_lock()
        except Exception as exc:
            self.logger.debug("lock.release_failed", extra={"error": str(exc)})
        await anyio.sleep(POLL_INTERVAL_SECONDS)
        try:
            log_ctx.reset(token)
        except Exception:
            pass

    @asynccontextmanager
    async def _polling_iteration_context(self):
        token = log_ctx.set({"brand": self.brand.value, "iteration_id": uuid.uuid4().hex[:8]})
        try:
            yield
        except Exception:
            self.logger.warning("poller.iteration_failed", exc_info=True)
        finally:
            await self._cleanup_iteration(token)

    async def _run_polling_cycle(self) -> None:
        async with (
            session_local() as session,
            self.job_queue.context(),
        ):
            tickets_repo = TicketsRepository(session)
            events_repo = EventsRepository(session)
            checkpoints_repo = CheckpointsRepository(session)
            async with session.begin():
                await self._poll_once(
                    session,
                    tickets_repo,
                    events_repo,
                    checkpoints_repo,
                    f"tickets_cursor:{self.brand.value}",
                    f"events_cursor:{self.brand.value}",
                )
                self.logger.debug("poller.iteration_done")
