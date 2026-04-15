import logging.config

import uvicorn

from src import config
from src.logs import LogEnvironment, build_logging_config
from src.web_admin.app import create_app

if __name__ == "__main__":
    app_settings = config.AppSettings.load()
    config.app_settings.set(app_settings)

    log_env = LogEnvironment.DEV if app_settings.app_debug else LogEnvironment.PROD
    logging.config.dictConfig(build_logging_config(env=log_env, json_logs=True))

    uvicorn.run(
        create_app(app_settings),
        host=app_settings.web.host,
        port=app_settings.web.port,
    )
