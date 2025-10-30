import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "brand": getattr(record, "brand", None),
            "ticket_id": getattr(record, "ticket_id", None),
            "job_type": getattr(record, "job_type", None),
            "iteration_id": getattr(record, "iteration_id", None),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False)
