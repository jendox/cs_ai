from datetime import datetime
from enum import Enum
from typing import Annotated, Any, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _normalize_status(value: str | None) -> str | None:
    return None if value is None else str(value).replace("-", "_").upper()


def _upper_case(value: str | None) -> str | None:
    return None if value is None else value.upper()


OptionalStr: TypeAlias = Annotated[str | None, Field(default=None)]
OptionalInt: TypeAlias = Annotated[int | None, Field(default=None)]
OptionalBool: TypeAlias = Annotated[bool | None, Field(default=None)]
OptionalDatetime: TypeAlias = Annotated[datetime | None, Field(default=None)]
OptionalIntList: TypeAlias = Annotated[list[int] | None, Field(default=None)]
OptionalStrList: TypeAlias = Annotated[list[str] | None, Field(default=None)]

AGENT_IDS = {
    372174069320,
    # test
    23063989634716,  # Anna
}


class Brand(int, Enum):
    # CLEOCORA = 13102068919196
    # SMARTPARTS = 360001509619
    # SUPERSELF = 360001148379
    # TEST
    SUPERSELF = 23064017794844
    SMARTPARTS = 23063999037340

    @property
    def short(self) -> str:
        if self == Brand.SUPERSELF:
            return "SS"
        if self == Brand.SMARTPARTS:
            return "SP"
        return "??"


class TicketStatus(str, Enum):
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


class TicketPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TicketType(str, Enum):
    QUESTION = "QUESTION"
    INCIDENT = "INCIDENT"
    PROBLEM = "PROBLEM"
    TASK = "TASK"


NormalizedTicketStatus: TypeAlias = Annotated[
    TicketStatus,
    BeforeValidator(_normalize_status),
]
NormalizedTicketPriority: TypeAlias = Annotated[
    TicketPriority,
    BeforeValidator(_upper_case),
]
NormalizedTicketType: TypeAlias = Annotated[
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
            ensure_ascii=True,
            exclude_none=True,
            by_alias=True,
        )

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_none=True,
            by_alias=True,
            mode="json",
        )
