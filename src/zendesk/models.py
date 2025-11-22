import hashlib
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, model_validator

from src import datetime_utils
from src.libs.zendesk_client.models import (
    AGENT_IDS,
    OptionalDatetime,
    OptionalInt,
    OptionalStr,
)

ROBOT_TAG_RE = re.compile(r"@robot(\b|:)", re.IGNORECASE)


def get_md5_hash(secret: str) -> str:
    return hashlib.md5(secret.encode()).hexdigest()


class EventKind(StrEnum):
    COMMENT_PUBLIC = "comment_public"
    COMMENT_PRIVATE = "comment_private"
    STATUS_CHANGE = "status_change"


class EventSourceType(StrEnum):
    COMMENT = "comment"
    STATUS = "status"


class EventAuthorRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


def validate_source_id(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return f"status:{datetime_utils.dt_to_iso(value)}"
    return value


class Event(BaseModel):
    event_key: OptionalStr
    ticket_id: OptionalInt
    source_type: EventSourceType
    source_id: Annotated[OptionalStr, BeforeValidator(validate_source_id)]
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
            self.has_robot_tag = bool(self.body and ROBOT_TAG_RE.search(self.body))
        return self

    @model_validator(mode="after")
    def set_author_role(self) -> Self:
        if self.source_type == EventSourceType.STATUS:
            self.author_role = EventAuthorRole.SYSTEM
        elif self.author_id in AGENT_IDS:
            self.author_role = EventAuthorRole.AGENT
        elif self.author_id is None:
            self.author_role = EventAuthorRole.UNKNOWN
        else:
            self.author_role = EventAuthorRole.USER
        return self

    @model_validator(mode="after")
    def set_event_key(self) -> Self:
        self.event_key = get_md5_hash(
            f"{self.ticket_id}:{self.source_type.value}:{self.source_id}",
        )
        return self
