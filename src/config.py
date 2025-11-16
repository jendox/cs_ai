from contextvars import ContextVar
from enum import Enum
from typing import Self, Literal

from pydantic import BaseModel, EmailStr, SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

app_settings: ContextVar["AppSettings"] = ContextVar("app_settings")


class ZendeskSettings(BaseModel):
    email: EmailStr
    token: SecretStr
    subdomain: str
    review_mode: bool = True


class RabbitMQSettings(BaseModel):
    user: str
    password: SecretStr
    host: str
    port: int

    @property
    def amqp_url(self) -> str:
        return f"amqp://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/"


class AmazonSettings(BaseModel):
    lwa_client_id: SecretStr
    lwa_client_secret: SecretStr
    lwa_refresh_token: SecretStr


class TelegramSettings(BaseModel):
    bot_token: SecretStr
    chat_id: int
    username: str
    enabled: bool = True
    min_level: str = "CRITICAL"


class PostgresSettings(BaseModel):
    user: str
    password: SecretStr
    host: str
    port: int
    db: str

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )


# ========== LLM SETTINGS ==========

class LLMProvider(str, Enum):
    OPENAI = "openai"
    GOOGLE = "google"
    ANTHROPIC = "anthropic"


class LLMProviderSettings(BaseModel):
    api_key: SecretStr
    base_url: str | None = None
    models: list[str]
    default_model: str


class GoogleProviderSettings(LLMProviderSettings):
    models: list[str] = Field(default=["gemini-1.5-flash"])
    default_model: str = "gemini-1.5-flash"


class OpenAIProviderSettings(LLMProviderSettings):
    models: list[str] = Field(default=["gpt-4.1-mini"])
    default_model: str = "gpt-4.1-mini"


class AnthropicProviderSettings(LLMProviderSettings):
    models: list[str] = Field(default=["anthropic:claude-3.5-sonnet"])
    default_model: str = "anthropic:claude-3.5-sonnet"


class LLMSettings(BaseModel):
    google: GoogleProviderSettings | None = None
    openai: OpenAIProviderSettings | None = None
    anthropic: AnthropicProviderSettings | None = None

    default_provider: Literal[
        LLMProvider.OPENAI,
        LLMProvider.GOOGLE,
        LLMProvider.ANTHROPIC,
    ] = LLMProvider.GOOGLE

    @property
    def default(self) -> LLMProviderSettings:
        return self._get_provider(self.default_provider)

    def _get_provider(self, provider: LLMProvider) -> LLMProviderSettings:
        return {
            LLMProvider.OPENAI: self.openai,
            LLMProvider.GOOGLE: self.google,
            LLMProvider.ANTHROPIC: self.anthropic,
        }.get(provider)

    def set_default_provider(self, provider: LLMProvider) -> None:
        self.default_provider = provider


class AppSettings(BaseSettings):
    zendesk: ZendeskSettings
    rabbitmq: RabbitMQSettings
    amazon: AmazonSettings
    telegram: TelegramSettings
    postgres: PostgresSettings
    llm: LLMSettings

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    @classmethod
    def load(cls) -> Self:
        return cls()
