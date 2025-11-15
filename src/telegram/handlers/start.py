from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.db.models import UserRole

router = Router(name=__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, role: UserRole):
    role_emoji = {
        UserRole.SUPERADMIN: "👑",
        UserRole.ADMIN: "🛡",
        UserRole.USER: "👤",
        UserRole.ANONYMOUS: "❓",
    }.get(role, "❓")

    text_parts = [
        f"Привет, {message.from_user.full_name}!\n",
        f"Твоя роль: <b>{role.name.title()}</b> {role_emoji}\n",
        "Этот бот помогает администрировать ИИ и тикеты.\n",
        "<b>Доступные команды сейчас:</b>",
        "• /stats &lt;limit&gt; — статистика по тикетам с <code>observing=True</code>",
    ]
    if role.level >= UserRole.ADMIN.level:
        text_parts.extend([
            "• /ticket_observe &lt;id&gt; — добавить тикет в отслеживаемые: <code>observing=True</code>",
            "• /ticket_unobserve &lt;id&gt; — исключить тикет из отслеживаемых: <code>observing=False</code>",
            "\n",
        ])

    text_parts.extend([
        "В будущем здесь появятся:",
        "• /ticket &lt;id&gt; — информация по тикету",
        "• /rules — правила фильтра",
        "• /rule_add — добавление правила",
    ])

    await message.answer("\n".join(text_parts))
