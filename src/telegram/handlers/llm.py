from __future__ import annotations

import datetime
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from pydantic import ValidationError

from src.admin.services import ClassificationSettingsPatch, LLMAdminService, PromptAdminService, ResponseSettingsPatch
from src.ai.config import RuntimeClassificationSettings, RuntimeResponseSettings
from src.brands import Brand
from src.db.models import LLMPromptKey, UserRole
from src.telegram.context import log_context
from src.telegram.filters import THREE_PARAMS_PARTS_COUNT, TWO_PARAMS_PARTS_COUNT, RoleRequired
from src.telegram.handlers.utils import get_telegram_id
from src.telegram.prompt_parsing import (
    allowed_brand_tokens,
    allowed_prompt_key_tokens,
    parse_brand_token,
    parse_prompt_key_token,
)

logger = logging.getLogger("telegram_admin")

router = Router(name=__name__)


# =========================
# = RUNTIME-НАСТРОЙКИ LLM =
# =========================

def _format_response_settings(settings: RuntimeResponseSettings) -> str:
    return (
        "<b>[Response]</b>\n"
        f"temperature: <code>{settings.temperature}</code>\n"
        f"top_p: <code>{settings.top_p}</code>\n"
        f"max_tokens: <code>{settings.max_tokens}</code>\n"
        f"provider: <code>{settings.provider or '-'}</code>\n"
        f"model: <code>{settings.model or '-'}</code>\n"
    )


def _format_classification_settings(settings: RuntimeClassificationSettings) -> str:
    return (
        "<b>[Classification]</b>\n"
        f"enabled: <code>{settings.enabled}</code>\n"
        f"threshold: <code>{settings.threshold}</code>\n"
        f"temperature: <code>{settings.temperature}</code>\n"
        f"max_tokens: <code>{settings.max_tokens}</code>\n"
    )


@router.message(Command("llm_settings"), RoleRequired(UserRole.USER))
async def cmd_llm_settings(message: Message) -> None:
    async with log_context(telegram_id=get_telegram_id(message)):
        async with LLMAdminService() as service:
            settings = await service.get_settings()
            text = (
                "<b>⚙️ Текущие LLM-настройки</b>\n\n"
                f"{_format_response_settings(settings.response)}\n"
                f"{_format_classification_settings(settings.classification)}"
            )
            await message.answer(text)


def _parse_args(text: str) -> tuple[dict[str, Any], list[str]]:
    parts = text.strip().split()
    result: dict[str, Any] = {}
    errors: list[str] = []

    for token in parts:
        if "=" not in token:
            errors.append(f"Ожидалось key=value, получено: <code>{token}</code>")
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            errors.append(f"Пустой ключ в паре: <code>{token}</code>")
            continue
        if not value:
            errors.append(f"Пустое значение для ключа: <code>{key}</code>")
            continue
        result[key] = value

    return result, errors


def _update_response_settings(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    updated_settings: dict[str, Any] = {}

    for key, value in params.items():
        if key in {"temperature", "top_p"}:
            try:
                updated_settings[key] = float(value)
            except ValueError:
                return None, f"Значение для <code>{key}</code> должно быть числом (float)."
        elif key == "max_tokens":
            try:
                updated_settings[key] = int(value)
            except ValueError:
                return None, "Значение для <code>max_tokens</code> должно быть целым числом."
        elif key in {"provider", "model"}:
            updated_settings[key] = value
        else:
            return None, f"Неизвестный параметр: <code>{key}</code>"

    return updated_settings, None


@router.message(Command("llm_response_set"), RoleRequired(UserRole.ADMIN))
async def cmd_llm_response_set(message: Message) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/llm_response_set temperature=0.3 max_tokens=900 "
                "provider=google model=gemini-2.5-flash-lite</code>",
            )
            return

        args_text = parts[1]
        params, errors = _parse_args(args_text)
        if errors:
            await message.answer("Ошибки в аргументах:\n" + "\n".join(errors))
            return

        updated_settings, error = _update_response_settings(params)
        if error is not None:
            await message.answer(error)
            return

        try:
            patch = ResponseSettingsPatch.model_validate(updated_settings)
            async with LLMAdminService() as service:
                new_settings = await service.update_response_settings(patch, updated_by=telegram_id)
                logger.info(
                    "llm_response_set.success",
                    extra={"telegram_id": telegram_id, "updated_settings": updated_settings},
                )
                await message.answer(
                    "✅ Настройки response-модели обновлены:\n\n" + _format_response_settings(new_settings),
                )
        except (ValidationError, ValueError) as exc:
            logger.error("llm_response_set.validation_error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка валидации настроек. Подробнее в логах.")
        except Exception as exc:
            logger.error("llm_response_set.error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка обновления настроек. Подробнее в логах.")


def _enabled_normalized_value(value: str) -> bool:
    normalized = value.lower()
    if normalized in {"yes", "y", "1", "true"}:
        return True
    if normalized in {"no", "n", "0", "false"}:
        return False
    raise ValueError()


def _parse_float_param(key: str, value: str) -> tuple[float | None, str | None]:
    try:
        return float(value), None
    except ValueError:
        return None, f"Значение для <code>{key}</code> должно быть числом (float)."


def _parse_int_param(key: str, value: str) -> tuple[int | None, str | None]:
    try:
        return int(value), None
    except ValueError:
        return None, "Значение для <code>max_tokens</code> должно быть целым числом."


def _parse_enabled_param(key: str, value: str) -> tuple[bool | None, str | None]:
    try:
        return _enabled_normalized_value(value), None
    except ValueError:
        return None, "Значение для <code>enabled</code> должно быть true/false (или 1/0, yes/no)."


_CLASSIFICATION_PARAM_PARSERS = {
    "temperature": _parse_float_param,
    "threshold": _parse_float_param,
    "max_tokens": _parse_int_param,
    "enabled": _parse_enabled_param,
}


def _update_classification_settings(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    updated_settings: dict[str, Any] = {}

    for key, value in params.items():
        parser = _CLASSIFICATION_PARAM_PARSERS.get(key)
        if parser is None:
            return None, f"Неизвестный параметр: <code>{key}</code>"

        parsed_value, error = parser(key, value)
        if error is not None:
            return None, error

        updated_settings[key] = parsed_value

    return updated_settings, None


@router.message(Command("llm_classification_set"), RoleRequired(UserRole.ADMIN))
async def cmd_llm_classification_set(message: Message) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/llm_classification_set enabled=false threshold=0.85 max_tokens=128</code>",
            )
            return

        args_text = parts[1]
        params, errors = _parse_args(args_text)
        if errors:
            await message.answer("Ошибки в аргументах:\n" + "\n".join(errors))
            return

        updated_settings, error = _update_classification_settings(params)
        if error is not None:
            await message.answer(error)
            return

        try:
            patch = ClassificationSettingsPatch.model_validate(updated_settings)
            async with LLMAdminService() as service:
                new_settings = await service.update_classification_settings(patch, updated_by=telegram_id)
                logger.info(
                    "llm_classification_set.success",
                    extra={"telegram_id": telegram_id, "updated_settings": updated_settings},
                )
                await message.answer(
                    "✅ Настройки классификатора обновлены:\n\n" + _format_classification_settings(new_settings),
                )
        except (ValidationError, ValueError) as exc:
            logger.error("llm_classification_set.validation_error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка валидации настроек. Подробнее в логах.")
        except Exception as exc:
            logger.error("llm_classification_set.error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка обновления настроек. Подробнее в логах.")


# =========================
# = ПРОМПТЫ: СПИСОК, INFO =
# =========================
def _format_date(datetime_: datetime.datetime | None) -> str:
    if datetime_ is None:
        return "-"
    return datetime_.strftime("%d-%m-%Y %H:%M:%S")


@router.message(Command("prompts"), RoleRequired(UserRole.USER))
async def cmd_prompts(message: Message) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        lines: list[str] = ["<b>🧠 LLM промпты</b>", ""]

        async with PromptAdminService() as service:
            by_brand: dict[Brand, list[LLMPromptKey]] = {}
            for item in service.list_prompt_keys():
                by_brand.setdefault(item.brand, []).append(item.key)

            for brand, keys in by_brand.items():
                brand_prompts: list[str] = []

                for key in keys:
                    prompt = await service.get_prompt(brand, key)
                    length = len(prompt.text or "")
                    brand_prompts.append(
                        f"• <code>{key.value}</code> — len: {length}, "
                        f"updated_by: <code>{prompt.updated_by}</code>, "
                        f"updated_at: <code>{_format_date(prompt.updated_at)}</code>",
                    )

                if brand_prompts:
                    lines.append(f"<b>{brand.name}</b> (id={brand.value})")
                    lines.extend(brand_prompts)
                    lines.append("")

        await message.answer("\n".join(lines) or "Промпты не найдены.")


@router.message(Command("prompt_info"), RoleRequired(UserRole.USER))
async def cmd_prompt_info(message: Message) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) < THREE_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/prompt_info &lt;brand&gt; &lt;key&gt;</code>\n"
                "Пример: <code>/prompt_info superself initial</code>",
            )
            return

        brand_token = parts[1]
        key_token = parts[2]

        brand, error = _parse_brand_token(brand_token)
        if error is not None:
            await message.answer(error)
            return

        key, error = _parse_prompt_key_token(key_token)
        if error is not None:
            await message.answer(error)
            return

        async with PromptAdminService() as service:
            prompt = await service.get_prompt(brand, key)

        length = len(prompt.text or "")

        await message.answer(
            "<b>ℹ️ Информация о промпте</b>\n\n"
            f"<b>brand</b>: <code>{brand.name}</code> (id={brand.value})\n"
            f"<b>key</b>: <code>{key.value}</code>\n"
            f"<b>length</b>: <code>{length}</code>\n"
            f"<b>updated_by</b>: <code>{prompt.updated_by}</code>\n"
            f"<b>updated_at</b>: <code>{_format_date(prompt.updated_at)}</code>\n"
            f"<b>comment</b>: {prompt.comment or '-'}",
        )


# ============================
# = ПРОМПТЫ: EXPORT / IMPORT =
# ============================
def _parse_brand_token(token: str) -> tuple[Brand | None, str | None]:
    brand = parse_brand_token(token)
    if brand is None:
        allowed = ", ".join(allowed_brand_tokens())
        return None, (
            "Неизвестный бренд.\n"
            f"Допустимые значения: <code>{allowed}</code>"
        )
    return brand, None


def _parse_prompt_key_token(token: str) -> tuple[LLMPromptKey | None, str | None]:
    key = parse_prompt_key_token(token)
    if key is None:
        allowed = ", ".join(allowed_prompt_key_tokens())
        return None, (
            "Неизвестный ключ промпта.\n"
            f"Допустимые значения: <code>{allowed}</code>"
        )
    return key, None


@router.message(Command("prompt_export"), RoleRequired(UserRole.USER))
async def cmd_prompt_export(message: Message) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) < THREE_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/prompt_export &lt;brand&gt; &lt;key&gt;</code>",
            )
            return

        brand_token = parts[1]
        key_token = parts[2]

        brand, error = _parse_brand_token(brand_token)
        if error:
            await message.answer(error)
            return

        key, error = _parse_prompt_key_token(key_token)
        if error:
            await message.answer(error)
            return

        async with PromptAdminService() as service:
            exported = await service.export_prompt(brand, key)

        document = BufferedInputFile(exported.content, filename=exported.filename)

        await message.answer_document(
            document,
            caption=(
                f"Промпт для brand={brand.name} key={key.value}\n"
                f"length={len(exported.prompt.text or '')}, updated_by={exported.prompt.updated_by}"
            ),
        )


@router.message(Command("prompt_import"), RoleRequired(UserRole.SUPERADMIN))
async def cmd_prompt_import_help(message: Message) -> None:
    await message.answer(
        "Чтобы импортировать промпт из файла:\n\n"
        "1) Подготовь .txt файл с текстом промпта.\n"
        "2) Отправь его боту как документ с подписью:\n"
        "<code>/prompt_import &lt;brand&gt; &lt;key&gt;</code>\n\n"
        "Пример:\n"
        "<code>/prompt_import superself initial</code>",
    )


@router.message(F.document & F.caption.startswith("/prompt_import"), RoleRequired(UserRole.SUPERADMIN))
async def handle_prompt_import_document(message: Message, bot: Bot) -> None:
    telegram_id = get_telegram_id(message)
    async with log_context(telegram_id=telegram_id):
        caption = message.caption or ""
        parts = caption.strip().split()
        if len(parts) < THREE_PARAMS_PARTS_COUNT:
            await message.answer(
                "Неверный формат подписи.\n"
                "Ожидалось: <code>/prompt_import &lt;brand&gt; &lt;key&gt;</code>",
            )
            return

        brand_token = parts[1]
        key_token = parts[2]

        brand, error = _parse_brand_token(brand_token)
        if error:
            await message.answer(error)
            return

        key, error = _parse_prompt_key_token(key_token)
        if error:
            await message.answer(error)
            return

        if not message.document:
            await message.answer("Не найден документ в сообщении.")
            return

        # Скачиваем файл в память
        file_buffer = await bot.download(message.document)
        content_bytes = file_buffer.read()
        try:
            text = content_bytes.decode()
        except UnicodeDecodeError:
            await message.answer("Не удалось декодировать файл как UTF-8.")
            return

        updated_by = (
            message.from_user.username or str(message.from_user.id)
            if message.from_user
            else str(telegram_id)
        )
        comment = f"Updated via Telegram by {updated_by}"

        async with PromptAdminService() as service:
            await service.import_prompt(
                brand=brand, key=key, text=text, updated_by=updated_by, comment=comment,
            )
        logger.info(
            "prompt_import.success",
            extra={
                "telegram_id": telegram_id,
                "brand": brand.name,
                "brand_id": brand.value,
                "key": key.value,
                "length": len(text),
            },
        )
        await message.answer(
            "✅ Промпт обновлён из файла:\n\n"
            f"<b>brand</b>: <code>{brand.name}</code>\n"
            f"<b>key</b>: <code>{key.value}</code>\n"
            f"<b>length</b>: <code>{len(text)}</code>",
        )
