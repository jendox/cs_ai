from enum import StrEnum
from typing import Any

from src.logs.filters import ContextFilter, DedupFilter, RedactFilter
from src.logs.formatters import JsonFormatter

__all__ = (
    "LogEnvironment",
    "build_logging_config",
)


class LogEnvironment(StrEnum):
    DEV = "development"
    PROD = "production"


def build_logging_config(
    env: LogEnvironment = LogEnvironment.PROD,
    json_logs: bool = True,
    telegram_handler=None,
) -> dict[str, Any]:
    console_formatter = {
        "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s "
                  "[brand=%(brand)s ticket=%(ticket_id)s job=%(job_type)s iter=%(iteration_id)s]",
    }
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG" if env == LogEnvironment.DEV else "INFO",
            "formatter": "console" if not json_logs else "json",
            "filters": ["ctx", "redact", "dedup"] if env == LogEnvironment.PROD else ["ctx", "redact"],
        },
    }
    if telegram_handler:
        handlers["telegram"] = {
            "()": lambda: telegram_handler,
            "level": "ERROR",
            "filters": ["ctx", "redact"],
        }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "ctx": {"()": ContextFilter},
            "redact": {"()": RedactFilter},
            "dedup": {"()": DedupFilter},
        },
        "formatters": {
            "console": {"format": console_formatter["format"]},
            "json": {"()": JsonFormatter},
        },
        "handlers": handlers,
        "root": {
            "level": "INFO",
            "handlers": list(handlers.keys()),
        },
        "loggers": {
            "httpx": {"level": "WARNING"},
            "aio_pika": {"level": "WARNING"},
            "zendesk_poller": {"level": "DEBUG" if env == LogEnvironment.DEV else "INFO"},
            "zendesk_client": {"level": "DEBUG" if env == LogEnvironment.DEV else "WARNING"},
            "jobs.queue": {"level": "DEBUG" if env == LogEnvironment.DEV else "INFO"},
            "initial_reply": {"level": "DEBUG" if env == LogEnvironment.DEV else "INFO"},
            "followup_reply": {"level": "DEBUG" if env == LogEnvironment.DEV else "INFO"},
            "db.repository": {"level": "WARNING"},
        },
    }
