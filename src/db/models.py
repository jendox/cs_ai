from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                raise ValueError(f"Invalid ISO datetime: {value!r}")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observing: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("idx_tickets_brand_status", "brand_id", "status", "updated_at"),
    )


class Event(Base):
    __tablename__ = "events"

    event_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey(
            "tickets.ticket_id",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    author_role: Mapped[str | None] = mapped_column(String)
    author_id: Mapped[int | None] = mapped_column(BigInteger)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    has_robot_tag: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str | None] = mapped_column(Text)
    body_hash: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_events_ticket_created", "ticket_id", "created_at"),
        Index("idx_events_kind_on_ticket", "ticket_id", "kind", "created_at"),
        Index("idx_events_robot_on_ticket", "ticket_id", "has_robot_tag", "is_private", "created_at"),
    )


class OurPost(Base):
    __tablename__ = "our_posts"

    post_key: Mapped[str] = mapped_column(String, primary_key=True)  # md5(ticket_id:body_hash)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey(
            "tickets.ticket_id",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    body_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_our_posts_ticket", "ticket_id", "created_at"),
    )


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Lock(Base):
    __tablename__ = "locks"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    holder: Mapped[str | None] = mapped_column(String)
    until: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("idx_locks_until", "until"),
    )


class TicketsFilterRule(Base):
    __tablename__ = "tickets_filter_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # тип правила: system_domain / system_address / subject_pattern / ...
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    # значение правила:
    # - домен: "amazon.com"; email: "no-reply@amazon.com"; тег: "servicemessage"
    # - regex-паттерн: r"^new customer message on"
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    # интерпретировать value как регулярное выражение?
    # (для subject_pattern / api_allowed_pattern почти всегда True,
    # но поле универсальное)
    is_regex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # brand_id: NULL -> глобальное правило, иначе действует только для бренда
    brand_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    # via_channel: NULL -> любой канал, иначе только, например, "email" / "api"
    via_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # активность правила (вместо физического удаления)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # человекочитаемый комментарий
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # кто создал / обновил (можно класть telegram username/id)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index(
            "ix_service_filter_rules_kind_brand_channel",
            "kind",
            "brand_id",
            "via_channel",
            "is_active",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ServiceFilterRule id={self.id} kind={self.kind!r} value={self.value!r} "
            f"brand_id={self.brand_id} via_channel={self.via_channel!r} is_active={self.is_active}>"
        )


# ========== TELEGRAM ==========

class UserRole(str, Enum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    USER = "user"
    ANONYMOUS = "anonymous"

    @property
    def level(self) -> int:
        return {
            UserRole.ANONYMOUS: 0,
            UserRole.USER: 1,
            UserRole.ADMIN: 2,
            UserRole.SUPERADMIN: 3,
        }[self]


user_role_enum = ENUM(UserRole, name="user_role_enum")


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(user_role_enum, nullable=False, default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
