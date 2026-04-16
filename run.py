import logging.config

import anyio

import src.app
from src import config
from src.logs import LogEnvironment, TelegramHandler, build_logging_config

if __name__ == "__main__":
    app_settings = config.get_app_settings()
    telegram_handler = None
    if app_settings.telegram.enabled:
        telegram_handler = TelegramHandler(
            bot_token=app_settings.telegram.bot_token.get_secret_value(),
            chat_id=app_settings.telegram.chat_id,
            level=app_settings.telegram.min_level,
        )
    log_env = LogEnvironment.DEV if app_settings.app_debug else LogEnvironment.PROD
    log_config = build_logging_config(env=log_env, json_logs=True, telegram_handler=telegram_handler)
    logging.config.dictConfig(log_config)
    anyio.run(src.app.app)
