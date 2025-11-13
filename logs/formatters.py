import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    DROP_FIELDS = {
        "name", "msg", "message", "args", "levelname", "levelno",
        "pathname", "filename", "module", "exc_info", "exc_text",
        "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "process",
        "processName", "stacklevel", "taskName",
    }

    CONTEXT_FIELDS = {"brand", "ticket_id", "job_type", "iteration_id"}

    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # контекстные поля (log_ctx)
        for field in self.CONTEXT_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                d[field] = val

        # EXTRA поля — всё, что пришло в record.__dict__
        for key, value in record.__dict__.items():
            if key in self.DROP_FIELDS:
                continue
            if key in d:
                continue  # не перезаписываем контекстные поля
            if key.startswith("_"):
                continue  # внутренние поля логгера

            d[key] = value

        # exception
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)

        return json.dumps(d, ensure_ascii=False)

# class JsonFormatter(logging.Formatter):
#     def format(self, record: logging.LogRecord) -> str:
#         d = {
#             "ts": datetime.now(timezone.utc).isoformat(),
#             "level": record.levelname,
#             "logger": record.name,
#             "msg": record.getMessage(),
#             "brand": getattr(record, "brand", None),
#             "ticket_id": getattr(record, "ticket_id", None),
#             "job_type": getattr(record, "job_type", None),
#             "iteration_id": getattr(record, "iteration_id", None),
#         }
#         if record.exc_info:
#             d["exc"] = self.formatException(record.exc_info)
#         return json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False)
