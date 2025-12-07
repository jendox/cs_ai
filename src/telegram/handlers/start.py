from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.db.models import UserRole

router = Router(name=__name__)


def _has_role(role: UserRole | None, required_role: UserRole) -> bool:
    return bool(role and role.level >= required_role.level)


def _add_description(
    telegram_id: int,
    username: str,
    role: UserRole,
) -> list[str]:
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
            f"Для добавления предоставь админу свой id: {telegram_id}.",
        ])

    return text


def _add_stats_commands(role: UserRole) -> list[str]:
    if role.level < UserRole.USER.level:
        return []

    return [
        "<b>Статистика</b>",
        "• /stats &lt;limit&gt; — статистика по тикетам с <code>observing=True</code>",
    ]


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
        ])

    return text


def _add_users_commands(role: UserRole) -> list[str]:
    text = []
    if role.level >= UserRole.USER.level:
        text.extend([
            "<b>Пользователи</b>",
            "• /me — информация о тебе",
        ])

    if role.level >= UserRole.ADMIN.level:
        text.extend([
            "• /users — список активных пользователей",
            "• /users all — все пользователи (включая деактивированных)",
            "• /add_user &lt;telegram_id&gt; &lt;role&gt; [username] — "
            "добавить/обновить пользователя с указанной ролью",
            "• /del_user &lt;telegram_id&gt; — деактивировать пользователя",
        ])

    if role.level >= UserRole.SUPERADMIN.level:
        text.extend([
            "• /set_role &lt;telegram_id&gt; &lt;role&gt; — сменить роль (user ↔ admin)",
        ])

    return text


def _add_catalog_commands(role: UserRole) -> list[str]:
    if role.level < UserRole.ADMIN.level:
        return []

    return [
        "<b>Каталог</b>",
        "• /sync_catalog &lt;brand&gt; — синхронизация каталога",
    ]


def _add_llm_commands_preview(role: UserRole) -> list[str]:
    sections: list[tuple[UserRole, list[str]]] = [
        (
            UserRole.USER,
            [
                "<b>LLM / Промпты</b>",
                "• /llm_settings — показать текущие LLM-настройки",
            ],
        ),
        (
            UserRole.ADMIN,
            [
                "• /llm_response_set key=value — изменить настройки генератора ответов модели",
                "• /llm_classification_set key=value — изменить настройки классификатора",
            ],
        ),
        (
            UserRole.USER,
            [
                "• /prompts — список всех промптов по брендам",
                "• /prompt_info &lt;brand&gt; &lt;key&gt; — метаинформация по промпту",
                "• /prompt_export &lt;brand&gt; &lt;key&gt; — выгрузить промпт в файл",
            ],
        ),
        (
            UserRole.SUPERADMIN,
            [
                "• /prompt_import — инструкция по импорту промпта из файла",
            ],
        ),
    ]

    text: list[str] = []
    for min_role, lines in sections:
        if role.level >= min_role.level:
            text.extend(lines)
    return text


def _add_zendesk_commands(role: UserRole) -> list[str]:
    if role.level < UserRole.ADMIN.level:
        return []

    return [
        "<b>Zendesk</b>",
        "• /zendesk_mode — текущий режим комментариев (internal/public)",
        "• /zendesk_mode_set &lt;internal|public&gt; — сменить режим комментариев",
    ]


def _build_main_menu(role: UserRole) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if role.level >= UserRole.USER.level:
        builder.button(text="📊 Статистика", callback_data="menu_stats")
        builder.button(text="🎫 Тикеты", callback_data="menu_tickets")

    if role.level >= UserRole.ADMIN.level:
        builder.button(text="👥 Пользователи", callback_data="menu_users")
        builder.button(text="📦 Каталог", callback_data="menu_catalog")
        builder.button(text="🛠️ Zendesk", callback_data="menu_zendesk")

    if role.level >= UserRole.USER.level:
        builder.button(text="🤖 LLM / Промпты", callback_data="menu_llm")

    builder.adjust(2)
    return builder.as_markup()


async def _send_main_menu(message: Message, role: UserRole) -> None:
    user = message.from_user
    username = user.full_name if user else "Unknown"
    telegram_id = user.id if user else 0

    text_parts = _add_description(telegram_id, username, role)
    text_parts.extend(_add_stats_commands(role))
    text_parts.extend(_add_tickets_commands(role))
    text_parts.extend(_add_users_commands(role))
    text_parts.extend(_add_catalog_commands(role))
    text_parts.extend(_add_llm_commands_preview(role))
    text_parts.extend(_add_zendesk_commands(role))

    await message.answer(
        "\n".join(text_parts),
        reply_markup=_build_main_menu(role),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, role: UserRole) -> None:
    await _send_main_menu(message, role)


@router.message(Command("menu"))
async def cmd_menu(message: Message, role: UserRole) -> None:
    await _send_main_menu(message, role)


# ===================================
# = Callback-хендлеры главного меню =
# ===================================
def _back_to_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В главное меню", callback_data="menu_main")
    return builder.as_markup()


async def _send_section_preview(
    callback: CallbackQuery,
    *,
    role: UserRole,
    required_role: UserRole,
    no_rights_message: str,
    title: str,
    body_lines: list[str],
) -> None:
    await callback.answer()

    if not _has_role(role, required_role):
        if callback.message:
            await callback.message.answer(no_rights_message)
        return

    if not callback.message:
        return

    lines = [f"<b>{title}</b>", ""]
    lines.extend(body_lines)

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=_back_to_main_menu_keyboard(),
    )


@router.callback_query(F.data == "menu_main")
async def cb_menu_main(callback: CallbackQuery, role: UserRole) -> None:
    await callback.answer()
    if callback.message:
        await _send_main_menu(callback.message, role)


@router.callback_query(F.data == "menu_stats")
async def cb_menu_stats(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.USER,
        no_rights_message="❌ Недостаточно прав для просмотра статистики.",
        title="📊 Раздел: Статистика",
        body_lines=_add_stats_commands(role),
    )


@router.callback_query(F.data == "menu_tickets")
async def cb_menu_tickets(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.USER,
        no_rights_message="❌ Недостаточно прав для работы с тикетами.",
        title="🎫 Раздел: Тикеты",
        body_lines=_add_tickets_commands(role),
    )


@router.callback_query(F.data == "menu_users")
async def cb_menu_users(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.ADMIN,
        no_rights_message="❌ Недостаточно прав для управления пользователями.",
        title="👥 Раздел: Пользователи",
        body_lines=_add_users_commands(role),
    )


@router.callback_query(F.data == "menu_catalog")
async def cb_menu_catalog(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.ADMIN,
        no_rights_message="❌ Недостаточно прав для управления каталогом.",
        title="📦 Раздел: Каталог",
        body_lines=_add_catalog_commands(role),
    )


@router.callback_query(F.data == "menu_llm")
async def cb_menu_llm(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.USER,
        no_rights_message="❌ Недостаточно прав для управления LLM/промптами.",
        title="🤖 Раздел: LLM / Промпты",
        body_lines=_add_llm_commands_preview(role),
    )


@router.callback_query(F.data == "menu_zendesk")
async def cb_menu_zendesk(callback: CallbackQuery, role: UserRole) -> None:
    await _send_section_preview(
        callback,
        role=role,
        required_role=UserRole.ADMIN,
        no_rights_message="❌ Недостаточно прав для управления настройками Zendesk.",
        title="🛠️ Раздел: Zendesk",
        body_lines=_add_zendesk_commands(role),
    )
