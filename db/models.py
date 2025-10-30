from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
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

    ticket_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(Integer, nullable=False)
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
    author_id: Mapped[int | None] = mapped_column(Integer)
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
