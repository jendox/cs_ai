import hashlib
from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from src import datetime_utils
from src.libs.zendesk_client.models import Ticket


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class JobType(str, Enum):
    INITIAL_REPLY = "initial_reply"
    USER_REPLY = "user_reply"
    AGENT_DIRECTIVE = "agent_directive"
    TICKET_CLOSED = "ticket_closed"

    @classmethod
    def all(cls) -> set["JobType"]:
        return {cls.INITIAL_REPLY, cls.USER_REPLY, cls.AGENT_DIRECTIVE, cls.TICKET_CLOSED}


def make_dedup_key(*parts) -> str:
    key = ":".join(map(str, parts))
    return hashlib.md5(key.encode()).hexdigest()


class BaseMessage(BaseModel):
    dedup_key: str | None = None
    created_at: datetime = Field(default_factory=datetime_utils.utcnow)


class TicketIdMessage(BaseMessage):
    ticket_id: int


class InitialReplyMessage(BaseMessage):
    ticket: Ticket

    @model_validator(mode="after")
    def set_dedup_key(self) -> Self:
        ts_base = self.ticket.created_at or self.ticket.updated_at or datetime_utils.utcnow()
        self.dedup_key = make_dedup_key(self.ticket.id, JobType.INITIAL_REPLY.value, int(ts_base.timestamp()))
        return self


class UserReplyMessage(TicketIdMessage):
    source_id: str

    @model_validator(mode="after")
    def set_dedup_key(self) -> Self:
        self.dedup_key = make_dedup_key(self.ticket_id, JobType.USER_REPLY.value, self.source_id)
        return self


class AgentDirectiveMessage(TicketIdMessage):
    source_id: str

    @model_validator(mode="after")
    def set_dedup_key(self) -> Self:
        self.dedup_key = make_dedup_key(self.ticket_id, JobType.AGENT_DIRECTIVE.value, self.source_id)
        return self


class TicketClosedMessage(TicketIdMessage):
    @model_validator(mode="after")
    def set_dedup_key(self) -> Self:
        self.dedup_key = make_dedup_key(self.ticket_id, JobType.TICKET_CLOSED.value)
        return self
