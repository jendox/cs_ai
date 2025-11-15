from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.db.repositories import TicketsRepository
from src.telegram.decorators import with_repository
from src.telegram.filters import RoleRequired, TicketId

router = Router(name=__name__)


@router.message(Command("ticket_observe"), TicketId(), RoleRequired(UserRole.ADMIN))
@with_repository(TicketsRepository)
async def cmd_ticket_observe(message: Message, ticket_id: int, repo: TicketsRepository):
    await repo.mark_observed(ticket_id)
    await message.answer(
        f"Для тикета <code>{ticket_id}</code> установлено <b>observing=True</b>."
    )


@router.message(Command("ticket_unobserve"), TicketId(), RoleRequired(UserRole.ADMIN))
@with_repository(TicketsRepository)
async def cmd_ticket_unobserve(message: Message, ticket_id: int, repo: TicketsRepository):
    await repo.mark_unobserved(ticket_id)
    await message.answer(
        f"Для тикета <code>{ticket_id}</code> установлено <b>observing=False</b>."
    )
