import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, model_validator

import datetime_utils
from libs.zendesk_client.models import (
    AGENT_IDS,
    OptionalDatetime,
    OptionalInt,
    OptionalStr,
)


def get_md5_hash(secret: str) -> str:
    return hashlib.md5(secret.encode()).hexdigest()


class EventKind(str, Enum):
    COMMENT_PUBLIC = "comment_public"
    COMMENT_PRIVATE = "comment_private"
    STATUS_CHANGE = "status_change"


class EventSourceType(str, Enum):
    COMMENT = "comment"
    STATUS = "status"


class EventAuthorRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


def validate_source_id(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return f"status:{datetime_utils.dt_to_iso(value)}"
    return value


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


class Job(BaseModel):
    ticket_id: int
    job_type: JobType = JobType.INITIAL_REPLY
    payload_json: OptionalStr
    status: JobStatus = JobStatus.QUEUED
    run_at: OptionalDatetime
    attempts: int = 0
    visibility_deadline: OptionalDatetime
    created_at: datetime = datetime_utils.utcnow()
    updated_at: datetime = datetime_utils.utcnow()


class Event(BaseModel):
    event_key: str
    ticket_id: OptionalInt
    source_type: EventSourceType
    source_id: Annotated[str, BeforeValidator(validate_source_id)]
    kind: EventKind
    author_role: EventAuthorRole | None = None
    author_id: OptionalInt
    is_private: bool = False
    has_robot_tag: bool = False
    body: OptionalStr
    body_hash: OptionalStr
    created_at: OptionalDatetime
    inserted_at: OptionalDatetime

    @model_validator(mode="after")
    def compute_body_hash(self) -> Self:
        if self.body is not None:
            self.body_hash = get_md5_hash(self.body)
        return self

    @model_validator(mode="after")
    def set_has_robot_tag(self) -> Self:
        if self.body is not None:
            tag = re.compile(r"@robot(\b|:)", re.IGNORECASE)
            self.has_robot_tag = bool(self.body and tag.search(self.body))
        return self

    @model_validator(mode="after")
    def set_author_role(self) -> Self:
        if self.source_type == EventSourceType.STATUS:
            self.author_role = EventAuthorRole.SYSTEM
        elif self.author_id in AGENT_IDS:
            self.author_role = EventAuthorRole.AGENT
        else:
            self.author_role = EventAuthorRole.USER
        return self

    @model_validator(mode="after")
    def set_event_key(self) -> Self:
        self.event_key = get_md5_hash(
            f"{self.ticket_id}:{self.source_type.value}:{self.source_id}",
        )
        return self
