import os
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _normalize_status(value: str | None) -> str | None:
    return None if value is None else str(value).replace("-", "_").upper()


def _upper_case(value: str | None) -> str | None:
    return None if value is None else value.upper()


OptionalStr: type = Annotated[str | None, Field(default=None)]
OptionalInt: type = Annotated[int | None, Field(default=None)]
OptionalBool: type = Annotated[bool | None, Field(default=None)]
OptionalDatetime: type = Annotated[datetime | None, Field(default=None)]
OptionalIntList: type = Annotated[list[int] | None, Field(default=None)]
OptionalStrList: type = Annotated[list[str] | None, Field(default=None)]

AGENT_IDS = {
    372174069320,
    368681312759,
    # test
    23063989634716,  # Anna
    23064038312732,  # Julia
}


def _require_int_env(name: str, default: str | None = None) -> int:
    raw = os.getenv(name, default)
    if raw is None:
        raise RuntimeError(f"Missing required env var: {name}")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be int, got: {raw!r}") from exc


SUPERSELF_ID = _require_int_env("BRAND__SUPERSELF_ID", "23064017794844")
SMARTPARTS_ID = _require_int_env("BRAND__SMARTPARTS_ID", "23063999037340")
CLEOCORA_ID = _require_int_env("BRAND__CLEOCORA_ID", "23063999037000")


class Brand(IntEnum):
    SUPERSELF = SUPERSELF_ID
    SMARTPARTS = SMARTPARTS_ID
    CLEOCORA = CLEOCORA_ID

    @classmethod
    def supported(cls) -> list[Self]:
        raw = os.getenv("BRAND__SUPPORTED", "SUPERSELF")
        names = [x.strip().upper() for x in raw.split(",") if x.strip()]
        result: list[Self] = []
        for name in names:
            try:
                result.append(cls[name])
            except KeyError as exc:
                raise RuntimeError(f"Unknown brand in BRAND__SUPPORTED: {name}") from exc
        return result

    @property
    def short(self) -> str:
        if self == Brand.SUPERSELF:
            return "SS"
        if self == Brand.SMARTPARTS:
            return "SP"
        if self == Brand.CLEOCORA:
            return "CC"
        return "??"


class TicketStatus(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    PENDING = "PENDING"
    HOLD = "HOLD"
    SOLVED = "SOLVED"
    CLOSED = "CLOSED"
    DELETED = "DELETED"

    @classmethod
    def active(cls) -> set["TicketStatus"]:
        return {cls.NEW, cls.OPEN}

    @classmethod
    def unresolved(cls) -> set["TicketStatus"]:
        return {cls.NEW, cls.OPEN, cls.PENDING, cls.HOLD}

    @classmethod
    def all(cls) -> set["TicketStatus"]:
        return {cls.NEW, cls.OPEN, cls.PENDING, cls.HOLD, cls.SOLVED, cls.CLOSED}


class TicketPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TicketType(StrEnum):
    QUESTION = "QUESTION"
    INCIDENT = "INCIDENT"
    PROBLEM = "PROBLEM"
    TASK = "TASK"


NormalizedTicketStatus: type = Annotated[
    TicketStatus,
    BeforeValidator(_normalize_status),
]
NormalizedTicketPriority: type = Annotated[
    TicketPriority,
    BeforeValidator(_upper_case),
]
NormalizedTicketType: type = Annotated[
    TicketType,
    BeforeValidator(_upper_case),
]


class FromTo(BaseModel):
    address: OptionalStr
    name: OptionalStr
    id: OptionalInt
    title: OptionalStr


class Source(BaseModel):
    from_: FromTo = Field(alias="from")
    rel: OptionalStr
    to_: FromTo = Field(alias="to")

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class Via(BaseModel):
    channel: str
    source: Source


class Comment(BaseModel):
    id: OptionalInt
    author_id: OptionalInt
    body: OptionalStr
    html_body: OptionalStr
    public: OptionalBool
    created_at: OptionalDatetime
    attachments: list["Attachment"] = Field(default_factory=list)


class AttachmentThumbnail(BaseModel):
    id: OptionalInt
    file_name: OptionalStr
    content_type: OptionalStr
    size: OptionalInt
    content_url: OptionalStr
    mapped_content_url: OptionalStr


class Attachment(BaseModel):
    id: OptionalInt
    file_name: OptionalStr
    content_type: OptionalStr
    size: OptionalInt
    content_url: OptionalStr
    mapped_content_url: OptionalStr
    thumbnails: list[AttachmentThumbnail] = Field(default_factory=list)


class Ticket(BaseModel):
    brand: Brand | None = Field(default=None, alias="brand_id")
    comment: Comment | None = None
    comment_count: OptionalInt
    created_at: OptionalDatetime
    description: OptionalStr
    encoded_id: OptionalStr
    external_id: OptionalStr
    id: OptionalInt
    is_public: OptionalBool
    organization_id: OptionalInt
    priority: NormalizedTicketPriority | None = None
    raw_subject: OptionalStr
    requester_id: OptionalInt
    status: NormalizedTicketStatus | None = None
    subject: OptionalStr
    tags: OptionalStrList
    type: NormalizedTicketType | None = None
    updated_at: OptionalDatetime
    via: Via | None = None

    model_config = ConfigDict(
        validate_by_name=True,
        validate_by_alias=True,
    )

    def to_json_str(self) -> str:
        return self.model_dump_json(
            exclude_none=True,
            by_alias=True,
        )

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_none=True,
            by_alias=True,
            mode="json",
        )
