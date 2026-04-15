from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator

from src import datetime_utils


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
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


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


class PostChannel(StrEnum):
    INTERNAL = "internal"
    PUBLIC = "public"


post_channel_enum = ENUM(PostChannel, name="post_channel_enum")


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
    body: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[PostChannel] = mapped_column(post_channel_enum, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_our_posts_ticket", "ticket_id", "created_at"),
    )


class ReplyAttemptStatus(StrEnum):
    GENERATED = "generated"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    EMPTY_REPLY = "empty_reply"


reply_attempt_status_enum = ENUM(ReplyAttemptStatus, name="reply_attempt_status_enum")


class TicketReplyAttempt(Base):
    __tablename__ = "ticket_reply_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey(
            "tickets.ticket_id",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    brand_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[PostChannel] = mapped_column(post_channel_enum, nullable=False)
    status: Mapped[ReplyAttemptStatus] = mapped_column(reply_attempt_status_enum, nullable=False)

    body_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    zendesk_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iteration_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        Index("idx_reply_attempts_ticket_created", "ticket_id", "created_at"),
        Index("idx_reply_attempts_status_created", "status", "created_at"),
        Index("idx_reply_attempts_brand_created", "brand_id", "created_at"),
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
            f"<TicketFilterRule id={self.id} kind={self.kind!r} value={self.value!r} "
            f"brand_id={self.brand_id} via_channel={self.via_channel!r} is_active={self.is_active}>"
        )


# ========== LLM ==========

class LLMRuntimeSettingsKey(StrEnum):
    CLASSIFICATION = "classification"
    RESPONSE = "response"


llm_settings_key_enum = ENUM(LLMRuntimeSettingsKey, name="llm_settings_key_enum")


class LLMRuntimeSettings(Base):
    __tablename__ = "llm_runtime_settings"

    key: Mapped[LLMRuntimeSettingsKey] = mapped_column(llm_settings_key_enum, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LLMPromptKey(StrEnum):
    INITIAL_REPLY = "initial_reply"
    FOLLOWUP_REPLY = "followup_reply"
    CLASSIFICATION = "classification"


llm_prompt_key_enum = ENUM(LLMPromptKey, name="llm_prompt_key_enum")


class LLMPrompt(Base):
    __tablename__ = "llm_prompts"

    key: Mapped[LLMPromptKey] = mapped_column(llm_prompt_key_enum, primary_key=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=True)


class MerchantListing(Base):
    __tablename__ = "merchant_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    marketplace_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Amazon report GET_MERCHANT_LISTINGS_ALL_DATA keys
    asin: Mapped[str] = mapped_column(String(20), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    item_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fulfillment_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # aggregated text for search (item_name + item_description)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    # tsvector for FTS
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    # audit
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "brand_id",
            "marketplace_id",
            "asin",
            "seller_sku",
            name="uq_merchant_listings_brand_marketplace_asin_sku",
        ),
        Index(
            "ix_merchant_listings_brand_marketplace_asin",
            "brand_id",
            "marketplace_id",
            "asin",
        ),
        Index(
            "ix_merchant_listings_brand_marketplace_sku",
            "brand_id",
            "marketplace_id",
            "seller_sku",
        ),
        Index(
            "ix_merchant_listings_search_tsv",
            "search_tsv",
            postgresql_using="gin",
        ),
    )


# ========== ADMIN ==========


class UserRole(StrEnum):
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

    @classmethod
    def allowed_new_users(cls) -> list[Self]:
        return [cls.ADMIN, cls.USER]


user_role_enum = ENUM(UserRole, name="user_role_enum")


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[UserRole] = mapped_column(user_role_enum, nullable=False, default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=datetime_utils.utcnow(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=datetime_utils.utcnow(),
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(user_role_enum, nullable=False, default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=datetime_utils.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=datetime_utils.utcnow,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )


# ========== ZENDESK ==========

class ZendeskRuntimeSettings(Base):
    __tablename__ = "zendesk_runtime_settings"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    review_channel: Mapped[PostChannel] = mapped_column(
        post_channel_enum, nullable=False, default=PostChannel.INTERNAL,
    )
    updated_by: Mapped[str] = mapped_column(String(32), nullable=True, default="default")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=datetime_utils.utcnow(),
    )
