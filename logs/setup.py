from logs.filters import ContextFilter, DedupFilter, RedactFilter
from logs.formatters import JsonFormatter


def build_logging_config(env: str = "prod", json_logs: bool = True, telegram_handler=None):
    console_formatter = {
        "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s "
                  "[brand=%(brand)s ticket=%(ticket_id)s job=%(job_type)s iter=%(iteration_id)s]",
    }
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG" if env == "dev" else "INFO",
            "formatter": "console" if not json_logs else "json",
            "filters": ["ctx", "redact", "dedup"] if env == "prod" else ["ctx", "redact"],
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
            "zendesk_poller": {"level": "INFO"},
            "zendesk_client": {"level": "WARNING"},
            "jobs.queue": {"level": "INFO"},
            "db.repository": {"level": "WARNING"},
        },
    }
