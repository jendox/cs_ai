from __future__ import annotations

import logging
import typing

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db.models import UserRole
from src.db.repositories.telegram import TelegramUsersRepository, UserNotFound

if typing.TYPE_CHECKING:
    from src.telegram.admin import TelegramAdmin
from src.telegram.context import log_context
from src.telegram.decorators import with_repository
from src.telegram.filters import TWO_PARAMS_PARTS_COUNT, RoleRequired, TelegramId, UserArgs, UserContext

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


# =========================
# /me
# =========================

@router.message(Command("me"), RoleRequired(UserRole.USER))
@with_repository(TelegramUsersRepository)
async def cmd_me(
    message: Message,
    role: UserRole,
    repo: TelegramUsersRepository,
) -> None:
    telegram_id = message.from_user.id
    async with log_context(telegram_id=telegram_id):
        try:
            user = await repo.get_by_telegram_id(telegram_id)
        except UserNotFound:
            await message.answer(
                "⚠️ Ты авторизован, но запись в БД не найдена 🤔\n"
                f"<b>Твои права</b>: {role.name.title()}",
            )
            logger.warning(
                "me.user_not_found",
                extra={"telegram_id": telegram_id, "role": role.value},
            )
            return
        await message.answer(
            "ℹ️ Информация о тебе:\n\n"
            f"<b>Telegram name</b>: {message.from_user.full_name if message.from_user else '-'}\n"
            f"<b>telegram_id</b>: {user.telegram_id}\n"
            f"<b>username (в БД)</b>: {user.username or '-'}\n"
            f"<b>role</b>: {UserRole(user.role).name.lower()}\n"
            f"<b>is_active</b>: {'yes' if getattr(user, 'is_active', True) else 'no'}",
        )


# =========================
# /add_user <telegram_id> <role> [username] — UPSERT
# =========================

@router.message(Command("add_user"), UserArgs(), RoleRequired(UserRole.ADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_add_user(
    message: Message,
    role: UserRole,
    telegram_admin: TelegramAdmin,
    user_context: UserContext,
    repo: TelegramUsersRepository,
) -> None:
    """
    /add_user <telegram_id> <role> [username]

    Логика прав:
    - ADMIN может создавать/обновлять только USER'ов.
    - SUPERADMIN может создавать/обновлять любые роли.
    Upsert:
    - если пользователь есть — обновляем роль (при необходимости) и активируем;
    - если нет — создаём.
    """
    async with log_context(telegram_id=message.from_user.id):
        # только супер-админ может назначать ADMIN / SUPERADMIN
        if user_context.role in {UserRole.ADMIN, UserRole.SUPERADMIN} and role != UserRole.SUPERADMIN:
            await message.answer(
                "🚫 Назначать роли <b>ADMIN</b>/<b>SUPERADMIN</b> может только супер-админ.",
            )
            return

        extra = {
            "actor_telegram_id": message.from_user.id if message.from_user else None,
            "target_telegram_id": user_context.telegram_id,
            "target_username": user_context.username,
            "target_role": user_context.role.value,
        }

        try:
            try:
                # Пытаемся найти пользователя
                user = await repo.get_by_telegram_id(user_context.telegram_id)
                current_role = UserRole(user.role)

                # Обновляем роль, если изменилась
                if current_role != user_context.role:
                    await repo.set_role(user.id, user_context.role)

                # Активируем (на случай, если был деактивирован)
                await repo.activate(user.id)
                action = "обновлён"
            except UserNotFound:
                # Создаём нового
                user = await repo.create(
                    telegram_id=user_context.telegram_id,
                    username=user_context.username,
                    role=user_context.role,
                )
                action = "добавлен"

            # Инвалидируем кэш ролей в TelegramAdmin
            telegram_admin.invalidate_cache_role(user_context.telegram_id)

            await message.answer(
                f"✅ Пользователь {action}:\n\n"
                f"<b>username</b>: {user_context.username}\n"
                f"<b>telegram_id</b>: {user_context.telegram_id}\n"
                f"<b>user_role</b>: {user_context.role.name.lower()}",
            )
            logger.info(
                "add_user.success",
                extra={**extra, "action": action, "user_id": user.id},
            )
        except Exception as exc:
            logger.error("add_user.error", extra={**extra, "error": str(exc)})
            await message.answer("⚠️ Ошибка при добавлении/обновлении пользователя. Подробности в логах.")


# =========================
# /del_user <telegram_id> — soft delete + инвалидация
# =========================

@router.message(Command("del_user"), TelegramId(), RoleRequired(UserRole.ADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_del_user(
    message: Message,
    role: UserRole,
    telegram_admin: TelegramAdmin,
    target_telegram_id: int,
    repo: TelegramUsersRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        extra = {
            "actor_telegram_id": message.from_user.id if message.from_user else None,
            "target_telegram_id": target_telegram_id,
        }
        try:
            user = await repo.get_by_telegram_id(target_telegram_id)
            target_role = UserRole(user.role)
        except UserNotFound:
            await message.answer(f"Пользователь с telegram_id <code>{target_telegram_id}</code> не найден.")
            logger.info("del_user.not_found", extra=extra)
            return

        if target_role in {UserRole.ADMIN, UserRole.SUPERADMIN} and role != UserRole.SUPERADMIN:
            await message.answer("🚫 Удалять <b>ADMIN</b>/<b>SUPERADMIN</b> может только супер-админ.")
            return

        try:
            await repo.deactivate(user.id)
            # инвалидация кэша ролей
            telegram_admin.invalidate_cache_role(target_telegram_id)

            await message.answer(
                "✅ Пользователь деактивирован:\n\n"
                f"<b>telegram_id</b>: {target_telegram_id}\n"
                f"<b>role</b>: {target_role.name.lower()}",
            )
            logger.info(
                "del_user.success",
                extra={**extra, "target_role": target_role.value, "user_id": user.id},
            )
        except Exception as exc:
            logger.error("del_user.error", extra={**extra, "error": str(exc)})
            await message.answer("⚠️ Ошибка при удалении пользователя. Подробности в логах.")


# =========================
# /set_role <telegram_id> <role> — смена роли USER ↔ ADMIN + инвалидация
# =========================

@router.message(Command("set_role"), UserArgs(), RoleRequired(UserRole.SUPERADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_set_role(
    message: Message,
    role: UserRole,
    telegram_admin: TelegramAdmin,
    user_context: UserContext,
    repo: TelegramUsersRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        extra = {
            "actor_telegram_id": message.from_user.id if message.from_user else None,
            "target_telegram_id": user_context.telegram_id,
            "target_role": user_context.role.value,
        }
        if user_context.role not in {UserRole.USER, UserRole.ADMIN}:
            await message.answer(
                "🚫 Команда /set_role поддерживает только роли <b>user</b> и <b>admin</b>.",
            )
            return
        try:
            user = await repo.get_by_telegram_id(user_context.telegram_id)
        except UserNotFound:
            await message.answer(
                f"🚫 Пользователь с telegram_id <code>{user_context.telegram_id}</code> не найден.",
            )
            logger.info("set_role.not_found", extra=extra)
            return

        current_role = UserRole(user.role)

        if current_role == UserRole.SUPERADMIN:
            await message.answer("🚫 Нельзя менять роль <b>SUPERADMIN</b> через эту команду.")
            return
        if current_role not in {UserRole.USER, UserRole.ADMIN}:
            await message.answer(
                "🚫 Через /set_role можно менять только роли <b>user</b> ↔ <b>admin</b>.",
            )
            return
        if current_role == user_context.role:
            await message.answer(
                f"ℹ️ Роль пользователя уже <b>{user_context.role.name.lower()}</b>.",
            )
            return
        try:
            await repo.set_role(user.id, user_context.role)
            # инвалидация кэша ролей
            telegram_admin.invalidate_cache_role(user_context.telegram_id)

            await message.answer(
                "✅ Роль пользователя изменена:\n\n"
                f"<b>telegram_id</b>: {user_context.telegram_id}\n"
                f"<b>old_role</b>: {current_role.name.lower()}\n"
                f"<b>new_role</b>: {user_context.role.name.lower()}",
            )
            logger.info(
                "set_role.success",
                extra={
                    **extra,
                    "old_role": current_role.value,
                    "new_role": user_context.role.value,
                    "user_id": user.id,
                },
            )
        except Exception as exc:
            logger.error("set_role.error", extra={**extra, "error": str(exc)})
            await message.answer("⚠️ Ошибка при смене роли пользователя. Подробности в логах.")


# =========================
# /users / /users all
# =========================

@router.message(Command("users"), RoleRequired(UserRole.ADMIN))
@with_repository(TelegramUsersRepository)
async def cmd_list_users(
    message: Message,
    role: UserRole,
    repo: TelegramUsersRepository,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        text = (message.text or "").strip().lower()
        parts = text.split()
        include_inactive = len(parts) >= TWO_PARAMS_PARTS_COUNT and str(parts[1]).lower() == "all"

        users = await repo.list_users(include_inactive=include_inactive)

        if not users:
            if include_inactive:
                await message.answer("Пользователей в системе пока нет.")
            else:
                await message.answer("Активных пользователей пока нет.")
            return

        rows: list[list[str]] = []
        header = ["ID", "TG_ID", "Username", "Role", "Active"]
        rows.append(header)

        for user in users:
            try:
                user_role = UserRole(user.role)
                role_name = user_role.name.lower()
            except Exception:
                role_name = str(user.role)

            rows.append([
                str(user.id),
                str(user.telegram_id),
                (user.username or "-"),
                role_name,
                "yes" if getattr(user, "is_active", True) else "no",
            ])

        col_widths = [max(len(row[i]) for row in rows) for i in range(len(header))]

        def fmt_row(row: list[str]) -> str:
            return "  ".join(val.ljust(col_widths[i]) for i, val in enumerate(row))

        table_text = "\n".join(fmt_row(r) for r in rows)

        prefix = "<b>👥 Список пользователей</b>\n"
        if include_inactive:
            prefix += "(включая деактивированных)\n\n"
        else:
            prefix += "(только активные)\n\n"

        await message.answer(
            prefix
            + "<pre>\n"
            + table_text
            + "\n</pre>",
        )
