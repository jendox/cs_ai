import json
import logging
from html import escape
from datetime import UTC, datetime


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
        fields = self._build_base(record)
        self._add_context_fields(record, fields)
        self._add_extra_fields(record, fields)
        self._add_exception(record, fields)

        return json.dumps(fields, ensure_ascii=False)

    @staticmethod
    def _build_base(record: logging.LogRecord) -> dict:
        return {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

    def _add_context_fields(self, record: logging.LogRecord, data: dict) -> None:
        for field in self.CONTEXT_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                data[field] = val

    def _add_extra_fields(self, record: logging.LogRecord, data: dict) -> None:
        for key, value in record.__dict__.items():
            if key in self.DROP_FIELDS:
                continue
            if key in data:
                continue  # не перезаписываем контекстные поля/базовые
            if key.startswith("_"):
                continue  # внутренние поля логгера

            data[key] = value

    def _add_exception(self, record: logging.LogRecord, data: dict) -> None:
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)


class TelegramFormatter(logging.Formatter):
    DROP_FIELDS = JsonFormatter.DROP_FIELDS | {"asctime"}
    CONTEXT_FIELDS = ("brand", "ticket_id", "job_type", "iteration_id")
    MAX_VALUE_LEN = 1200
    MAX_MESSAGE_LEN = 3800

    def format(self, record: logging.LogRecord) -> str:
        lines = [f"<b>{escape(record.levelname)}</b> {escape(record.name)}", escape(record.getMessage())]

        context_parts = []
        for field in self.CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                context_parts.append(f"{field}={self._stringify(value)}")
        if context_parts:
            lines.append(" ".join(context_parts))

        for key, value in record.__dict__.items():
            if key in self.DROP_FIELDS or key in self.CONTEXT_FIELDS:
                continue
            if key in {"name", "levelname", "msg", "message"}:
                continue
            if key.startswith("_"):
                continue
            lines.append(f"{escape(str(key))}: <code>{escape(self._stringify(value))}</code>")

        if record.exc_info:
            lines.append(f"<pre>{escape(self.formatException(record.exc_info))}</pre>")

        rendered = "\n".join(lines)
        if len(rendered) > self.MAX_MESSAGE_LEN:
            return f"{rendered[: self.MAX_MESSAGE_LEN - 1]}…"
        return rendered

    def _stringify(self, value: object) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except TypeError:
                text = str(value)

        if len(text) > self.MAX_VALUE_LEN:
            return f"{text[: self.MAX_VALUE_LEN - 1]}…"
        return text
