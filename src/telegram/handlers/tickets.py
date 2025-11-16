import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.db.repositories import TicketsRepository
from src.telegram.context import log_context
from src.telegram.decorators import with_repository
from src.telegram.filters import RoleRequired, TicketId

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


@router.message(Command("observe"), TicketId(), RoleRequired(UserRole.ADMIN))
@with_repository(TicketsRepository)
async def cmd_ticket_observe(message: Message, ticket_id: int, repo: TicketsRepository):
    async with log_context(telegram_id=message.from_user.id):
        await repo.set_observing(ticket_id, observing=True)
        await message.answer(
            f"Для тикета <code>{ticket_id}</code> установлено <b>observing=True</b>.",
        )
        logger.info("ticket.observe", extra={"ticket_id": ticket_id})


@router.message(Command("not_observe"), TicketId(), RoleRequired(UserRole.ADMIN))
@with_repository(TicketsRepository)
async def cmd_ticket_not_observe(message: Message, ticket_id: int, repo: TicketsRepository):
    async with log_context(telegram_id=message.from_user.id):
        await repo.set_observing(ticket_id, observing=False)
        await message.answer(
            f"Для тикета <code>{ticket_id}</code> установлено <b>observing=False</b>.",
        )
        logger.info("ticket.not_observe", extra={"ticket_id": ticket_id})
