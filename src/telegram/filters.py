from dataclasses import dataclass
from typing import Any

from aiogram.filters import Filter
from aiogram.types import Message

from src.db.models import UserRole

TWO_PARAMS_PARTS_COUNT = 2
THREE_PARAMS_PARTS_COUNT = 3
FOUR_PARAMS_PARTS_COUNT = 4


@dataclass(slots=True)
class UserContext:
    telegram_id: int
    role: UserRole
    username: str | None = None


class RoleRequired(Filter):

    def __init__(self, required: UserRole | None = None):
        self.required = required

    async def __call__(self, message: Message, role: UserRole | None = None, **kwargs) -> bool:
        if self.required is None:
            return True
        return role.level >= self.required.level


class TicketId(Filter):
    def __init__(self, required: bool = True) -> None:
        self.required = required

    async def __call__(self, message: Message, **data: dict[str, Any]) -> bool | dict[str, Any]:
        if not message.text:
            return not self.required
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            if self.required:
                await message.answer("Нужно указать id тикета: <code>123456</code>")
                return False
            return True

        try:
            ticket_id = int(parts[1].strip())
        except ValueError:
            if self.required:
                await message.answer("ID тикета должен быть числом: <code>123456</code>")
                return False
            return True

        return {"ticket_id": ticket_id}


class TelegramId(Filter):
    def __init__(self, required: bool = True) -> None:
        self.required = required

    async def __call__(self, message: Message, **data: dict[str, Any]) -> bool | dict[str, Any]:
        if not message.text:
            return not self.required

        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            if self.required:
                await message.answer("Нужно telegram_id: <code>123456789</code>")
                return False
            return True

        try:
            telegram_id = int(parts[1].strip())
        except ValueError:
            if self.required:
                await message.answer("telegram_id должен быть числом: <code>123456789</code>")
                return False
            return True

        return {"telegram_id": telegram_id}


# =========================
# Helpers для UserArgs
# =========================


def _user_args_missing_text(required: bool) -> tuple[UserContext | None, str | None, bool]:
    if required:
        message = (
            "Нужно указать параметры:\n"
            "<code>/add_user &lt;telegram_id&gt; &lt;role&gt; [username]</code>"
        )
        return None, message, False
    return None, None, True


def _user_args_missing_parts(required: bool) -> tuple[UserContext | None, str | None, bool]:
    if required:
        message = (
            "Нужно указать параметры:\n"
            "<code>/add_user &lt;telegram_id&gt; &lt;role&gt; [username]</code>"
        )
        return None, message, False
    return None, None, True


def _parse_telegram_id_token(token: str) -> tuple[int | None, str | None]:
    try:
        return int(token), None
    except ValueError:
        return None, "telegram_id должен быть числом: <code>123456789</code>"


def _build_allowed_role_map() -> dict[str, UserRole]:
    """
    Строим словарь:
        "user" -> UserRole.USER
        "superadmin" -> UserRole.SUPERADMIN
        и т.п.
    """
    mapping: dict[str, UserRole] = {}
    for role in UserRole.allowed_new_users():
        # Имя enum-а
        mapping[role.name.lower()] = role
        # Значение enum-а (у тебя оно такое же, но на будущее)
        mapping[role.value.lower()] = role
    return mapping


_ALLOWED_ROLE_MAP = _build_allowed_role_map()


def _parse_role_token(token: str) -> tuple[UserRole | None, str | None]:
    role_token = token.strip().lower()
    if role_token not in _ALLOWED_ROLE_MAP:
        allowed = ", ".join(sorted(_ALLOWED_ROLE_MAP.keys()))
        message = (
            "Неизвестная роль.\n"
            f"Допустимые значения: <code>{allowed}</code>"
        )
        return None, message
    return _ALLOWED_ROLE_MAP[role_token], None


def _parse_user_args(
    text: str | None,
    required: bool,
) -> tuple[UserContext | None, str | None, bool]:
    """
    Возвращает:
        (user_context, error_message, skip)

    skip = True  -> фильтр пропускает дальше без user_context (для required=False).
    error_message != None -> нужно отправить пользователю и вернуть False из фильтра.
    """
    normalized = (text or "").strip()
    if not normalized:
        return _user_args_missing_text(required)

    parts = normalized.split()
    if len(parts) < THREE_PARAMS_PARTS_COUNT:
        return _user_args_missing_parts(required)

    # telegram_id
    telegram_id, err = _parse_telegram_id_token(parts[1])
    if err is not None:
        return None, err, False

    assert telegram_id is not None  # для type checker-а

    # role
    target_role, err = _parse_role_token(parts[2])
    if err is not None:
        return None, err, False

    assert target_role is not None

    # username (опционально)
    if len(parts) >= FOUR_PARAMS_PARTS_COUNT:
        username = parts[3].strip()
    else:
        username = str(telegram_id)

    user_context = UserContext(
        telegram_id=telegram_id,
        role=target_role,
        username=username,
    )
    return user_context, None, False


class UserArgs(Filter):
    """
    Для /add_user и /set_role:
    /add_user <telegram_id> <role> [username]
    /set_role <telegram_id> <role> [username]
    """

    def __init__(self, required: bool = True) -> None:
        self.required = required

    async def __call__(self, message: Message, **data: dict[str, Any]) -> bool | dict[str, Any]:
        user_context, error, skip = _parse_user_args(message.text, self.required)

        if skip:
            return True

        if error is not None:
            await message.answer(error)
            return False

        if user_context is None:
            # На всякий случай, не ломаем пайплайн.
            return not self.required

        return {"user_context": user_context}
