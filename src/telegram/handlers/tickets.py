import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.db.repositories import TicketNotFound, TicketsRepository
from src.libs.zendesk_client.models import Brand
from src.telegram.context import log_context
from src.telegram.decorators import with_repository
from src.telegram.filters import RoleRequired, TicketId

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


@router.message(Command("observe"), RoleRequired(UserRole.ADMIN), TicketId())
@with_repository(TicketsRepository)
async def cmd_ticket_observe(
    message: Message,
    ticket_id: int,
    repo: TicketsRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        await repo.set_observing(ticket_id, observing=True)
        logger.info("ticket.observe", extra={"ticket_id": ticket_id})
        await message.answer(
            f"Для тикета <code>{ticket_id}</code> установлено <b><code>observing=True</code></b>.",
        )


@router.message(Command("not_observe"), RoleRequired(UserRole.ADMIN), TicketId())
@with_repository(TicketsRepository)
async def cmd_ticket_not_observe(
    message: Message,
    ticket_id: int,
    repo: TicketsRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        await repo.set_observing(ticket_id, observing=False)
        logger.info("ticket.not_observe", extra={"ticket_id": ticket_id})
        await message.answer(
            f"Для тикета <code>{ticket_id}</code> установлено <b><code>observing=False</code></b>.",
        )


@router.message(Command("ticket"), RoleRequired(UserRole.USER), TicketId())
@with_repository(TicketsRepository)
async def cmd_ticket_info(
    message: Message,
    ticket_id: int,
    repo: TicketsRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        try:
            ticket = await repo.get_ticket_by_id(ticket_id)
        except TicketNotFound:
            await message.answer(
                f"❌ Тикет с id <code>{ticket_id}</code> не найден.",
            )
            logger.info("ticket.info.not_found", extra={"ticket_id": ticket_id})
            return

        def format_date(dt):
            return dt.strftime("%d-%m-%Y %H:%M:%S") if dt else "-"

        try:
            brand_name = Brand(ticket.brand_id).name
        except Exception:
            brand_name = f"#{ticket.brand_id}"

        observing_status = "yes" if ticket.observing else "no"

        text = (
            "<b>ℹ️ Информация по тикету</b>\n\n"
            f"<b>ID</b>: <code>{ticket.ticket_id}</code>\n"
            f"<b>Brand</b>: {brand_name}\n"
            f"<b>Status</b>: {ticket.status}\n"
            f"<b>Observing</b>: <code>{observing_status}</code>\n"
            f"<b>Updated at</b>: {format_date(ticket.updated_at)}\n"
            f"<b>Last seen at</b>: {format_date(ticket.last_seen_at)}\n"
        )

        logger.info("ticket.info.success", extra={"ticket_id": ticket_id})

        await message.answer(text)
