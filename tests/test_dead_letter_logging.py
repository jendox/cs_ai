import logging

from src.jobs.models import JobType
from src.jobs.rabbitmq_queue import RabbitJobQueue
from src.logs.formatters import TelegramFormatter


class DummyMessage:
    def __init__(self, body: bytes, correlation_id: str | None = None):
        self.body = body
        self.correlation_id = correlation_id


def test_build_dead_log_extra_includes_payload_identifiers() -> None:
    message = DummyMessage(
        body=b'{"ticket_id":42,"source_id":"comment-7","dedup_key":"abc123","created_at":"2026-04-30T10:00:00Z"}',
        correlation_id="abc123",
    )

    extra = RabbitJobQueue._build_dead_log_extra(
        job_type=JobType.FOLLOWUP_REPLY,
        message=message,
        headers={"attempt": 4, "brand": 99, "job_type": JobType.FOLLOWUP_REPLY.value},
        payload={
            "ticket_id": 42,
            "source_id": "comment-7",
            "dedup_key": "abc123",
            "created_at": "2026-04-30T10:00:00Z",
        },
    )

    assert extra["routing_key"] == "followup_reply.99.dead"
    assert extra["attempt"] == 4
    assert extra["correlation_id"] == "abc123"
    assert extra["payload"]["ticket_id"] == 42
    assert extra["payload"]["source_id"] == "comment-7"
    assert "dedup_key" in extra["payload_preview"]


def test_telegram_formatter_renders_extra_fields() -> None:
    formatter = TelegramFormatter()
    record = logging.LogRecord(
        name="jobs.queue",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="msg.dead",
        args=(),
        exc_info=None,
    )
    record.brand = 99
    record.job_type = "followup_reply"
    record.ticket_id = 42
    record.correlation_id = "abc123"
    record.payload_preview = '{"ticket_id":42,"source_id":"comment-7"}'

    rendered = formatter.format(record)

    assert "<b>ERROR</b> jobs.queue" in rendered
    assert "msg.dead" in rendered
    assert "brand=99" in rendered
    assert "ticket_id=42" in rendered
    assert "correlation_id: <code>abc123</code>" in rendered
    assert "payload_preview" in rendered
