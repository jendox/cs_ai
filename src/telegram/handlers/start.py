from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.db.models import UserRole

router = Router(name=__name__)


def _add_description(username: str, role: UserRole) -> list[str]:
    role_emoji = {
        UserRole.SUPERADMIN: "👑",
        UserRole.ADMIN: "🛡",
        UserRole.USER: "👤",
        UserRole.ANONYMOUS: "❓",
    }.get(role, "❓")

    text = [
        f"Привет, {username}!\n",
        f"Твоя роль: <b>{role.name.title()}</b> {role_emoji}\n",
        "Этот бот помогает администрировать ИИ и тикеты.\n",
        "<b>Доступные команды сейчас:</b>",
    ]

    if role == UserRole.ANONYMOUS:
        text.extend([
            "❌ Неизвестным пользователям доступ к командам <b>запрещен</b>.",
        ])

    return text


def _add_stats_commands(role: UserRole) -> list[str]:
    text = []
    if role.level >= UserRole.USER.level:
        text.extend([
            "<b>Статистика</b>",
            "• /stats &lt;limit&gt; — статистика по тикетам с <code>observing=True</code>",
        ])

    return text


def _add_tickets_commands(role: UserRole) -> list[str]:
    text = []
    if role.level >= UserRole.USER.level:
        text.extend([
            "<b>Тикеты</b>",
            "• /ticket &lt;ticket_id&gt; — информация по тикету",
        ])

    if role.level >= UserRole.ADMIN.level:
        text.extend([
            "• /observe &lt;ticket_id&gt; — добавить тикет в отслеживаемые: <code>observing=True</code>",
            "• /not_observe &lt;ticket_id&gt; — исключить тикет из отслеживаемых: <code>observing=False</code>",
            "\n",
        ])

    return text


def _add_future_extensions() -> list[str]:
    return [
        "В будущем здесь появятся:",
        "• /rules — правила фильтра",
        "• /rule_add — добавление правила",
    ]


@router.message(CommandStart())
async def cmd_start(message: Message, role: UserRole):
    text_parts = _add_description(message.from_user.full_name, role)
    text_parts.extend(_add_stats_commands(role))
    text_parts.extend(_add_tickets_commands(role))
    text_parts.extend(_add_future_extensions())

    await message.answer("\n".join(text_parts))
