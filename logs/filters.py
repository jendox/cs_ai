import logging
import re
import time
from contextvars import ContextVar

log_ctx: ContextVar[dict | None] = ContextVar("log_ctx", default=None)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, v in log_ctx.get({}).items():
            setattr(record, k, v)
        return True


_SECRET_RE = re.compile(r'(?i)(token|secret|password|passwd|authorization)=[^\s&]+')


def _redact(s: str) -> str: return _SECRET_RE.sub(r'\1=[REDACTED]', s)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        return True


class DedupFilter(logging.Filter):
    def __init__(self, window_sec: int = 60, max_occurrences: int = 3):
        super().__init__()
        self.window = window_sec
        self.max_occurrences = max_occurrences
        self.cache: dict[tuple, tuple[int, float]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        now = time.time()
        count, first = self.cache.get(key, (0, now))
        if now - first > self.window:
            self.cache[key] = (1, now)
            return True
        count += 1
        self.cache[key] = (count, first)
        return count <= self.max_occurrences
