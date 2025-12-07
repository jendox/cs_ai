from __future__ import annotations

import datetime
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from src import datetime_utils
from src.ai.config import RuntimeClassificationSettings, RuntimeResponseSettings
from src.ai.config.prompt import LLMPrompt, LLMPromptStorage
from src.ai.context import LLMContext
from src.db.models import LLMPromptKey, UserRole
from src.libs.zendesk_client.models import Brand
from src.telegram.context import log_context
from src.telegram.filters import THREE_PARAMS_PARTS_COUNT, TWO_PARAMS_PARTS_COUNT, RoleRequired
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
async def cmd_llm_settings(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        response_settings = await llm_context.runtime_storage.get_response()
        classification_settings = await llm_context.runtime_storage.get_classification()
        text = (
            "<b>⚙️ Текущие LLM-настройки</b>\n\n"
            f"{_format_response_settings(response_settings)}\n"
            f"{_format_classification_settings(classification_settings)}"
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
async def cmd_llm_response_set(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        text = (message.text or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < TWO_PARAMS_PARTS_COUNT:
            await message.answer(
                "Использование:\n"
                "<code>/llm_response_set temperature=0.3 max_tokens=900 provider=openai model=gpt-5.1</code>",
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
            current_settings = await llm_context.runtime_storage.get_response()
            new_settings = current_settings.model_copy(update=updated_settings)
        except Exception as exc:
            logger.error("llm_response_set.validation_error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка валидации настроек. Подробнее в логах.")
            return
        await llm_context.runtime_storage.set_response(new_settings, message.from_user.id)
        logger.info(
            "llm_response_set.success",
            extra={"telegram_id": message.from_user.id, "updated_settings": updated_settings},
        )
        await message.answer(
            "✅ Настройки response-модели обновлены:\n\n" + _format_response_settings(new_settings),
        )


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
async def cmd_llm_classification_set(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
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
            current_settings = await llm_context.runtime_storage.get_classification()
            new_settings = current_settings.model_copy(update=updated_settings)
        except Exception as exc:
            logger.error("llm_classification_set.validation_error", extra={"error": str(exc)})
            await message.answer("⚠️ Ошибка валидации настроек. Подробнее в логах.")
            return
        await llm_context.runtime_storage.set_classification(new_settings, message.from_user.id)
        logger.info(
            "llm_classification_set.success",
            extra={"telegram_id": message.from_user.id, "updated_settings": updated_settings},
        )
        await message.answer(
            "✅ Настройки классификатора обновлены:\n\n" + _format_classification_settings(new_settings),
        )


# =========================
# = ПРОМПТЫ: СПИСОК, INFO =
# =========================
def _format_date(datetime_: datetime.datetime | None) -> str:
    if datetime_ is None:
        return "-"
    return datetime_.strftime("%d-%m-%Y %H:%M:%S")


async def _load_prompt(
    prompt_storage: LLMPromptStorage,
    key: LLMPromptKey,
    brand: Brand,
) -> LLMPrompt:
    if key == LLMPromptKey.INITIAL_REPLY:
        return await prompt_storage.initial_reply_prompt(brand)
    if key == LLMPromptKey.FOLLOWUP_REPLY:
        return await prompt_storage.followup_reply_prompt(brand)
    if key == LLMPromptKey.CLASSIFICATION:
        return await prompt_storage.classification_prompt(brand)
    raise ValueError(f"Unsupported LLMPromptKey: {key}")


@router.message(Command("prompts"), RoleRequired(UserRole.USER))
async def cmd_prompts(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
        lines: list[str] = ["<b>🧠 LLM промпты</b>", ""]

        for brand in Brand.supported():
            brand_prompts: list[str] = []
            for key in LLMPromptKey:
                prompt = await _load_prompt(llm_context.prompt_storage, key, brand)
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
async def cmd_prompt_info(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
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

        prompt = await _load_prompt(llm_context.prompt_storage, key, brand)
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
async def cmd_prompt_export(
    message: Message,
    llm_context: LLMContext,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
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

        prompt = await _load_prompt(llm_context.prompt_storage, key, brand)
        content = (prompt.text or "").encode()

        timestamp = datetime_utils.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{brand.name.lower()}_{key.value}_{timestamp}.txt"

        document = BufferedInputFile(content, filename=filename)

        await message.answer_document(
            document,
            caption=(
                f"Промпт для brand={brand.name} key={key.value}\n"
                f"length={len(prompt.text or '')}, updated_by={prompt.updated_by}"
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
async def handle_prompt_import_document(
    message: Message,
    llm_context: LLMContext,
    bot: Bot,
) -> None:
    async with log_context(telegram_id=message.from_user.id):
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

        existing = await _load_prompt(llm_context.prompt_storage, key, brand)
        existing.text = text
        existing.updated_by = message.from_user.username or str(message.from_user.id)
        existing.comment = f"Updated via Telegram by {existing.updated_by}"

        await llm_context.prompt_storage.save(existing, user_id=message.from_user.id)
        logger.info(
            "prompt_import.success",
            extra={
                "telegram_id": message.from_user.id,
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
