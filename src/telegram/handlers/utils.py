from aiogram.types import Message


def get_telegram_id(message: Message) -> int:
    return message.from_user.id if message.from_user else 0
