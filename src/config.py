from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import BaseModel, EmailStr, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.brands import Brand


class ZendeskSettings(BaseModel):
    email: EmailStr
    token: SecretStr
    subdomain: str


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

class LLMProvider(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"
    ANTHROPIC = "anthropic"


class LLMProviderSettings(BaseModel):
    api_key: SecretStr
    base_url: str | None = None
    model: str


class GoogleProviderSettings(LLMProviderSettings):
    model: str = "gemini-2.5-flash-lite"


class OpenAIProviderSettings(LLMProviderSettings):
    model: str = "gpt-4.1-mini"


class AnthropicProviderSettings(LLMProviderSettings):
    model: str = "anthropic:claude-3.5-sonnet"


class LLMSettings(BaseModel):
    google: GoogleProviderSettings | None = None
    openai: OpenAIProviderSettings | None = None
    anthropic: AnthropicProviderSettings | None = None

    default_provider: LLMProvider = LLMProvider.GOOGLE

    def get_provider_settings(
        self,
        provider: LLMProvider | None,
    ) -> LLMProviderSettings:
        provider = provider or self.default_provider
        mapping: dict[LLMProvider, LLMProviderSettings | None] = {
            LLMProvider.OPENAI: self.openai,
            LLMProvider.GOOGLE: self.google,
            LLMProvider.ANTHROPIC: self.anthropic,
        }
        settings = mapping.get(provider)
        if settings is None:
            raise ValueError(f"Provider {provider} is not configured in LLMSettings")
        return settings

    def set_default_provider(self, provider: LLMProvider) -> None:
        self.default_provider = provider


class MCPSettings(BaseModel):
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"


class WebAdminSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    session_secret: SecretStr
    bootstrap_username: str
    bootstrap_password: SecretStr
    cookie_secure: bool = False


class BrandSettings(BaseModel):
    superself: int
    smartparts: int
    cleocora: int
    supported: list[Brand] = [Brand.SUPERSELF]

    def id_for(self, brand: Brand) -> int:
        return self._brand_to_id()[brand]

    def brand_for_id(self, brand_id: int) -> Brand | None:
        return self._id_to_brand().get(brand_id)

    def require_brand_for_id(self, brand_id: int) -> Brand:
        brand = self.brand_for_id(brand_id)
        if brand is None:
            raise ValueError(f"Unknown brand_id: {brand_id}")
        return brand

    def _brand_to_id(self) -> dict[Brand, int]:
        return {
            Brand.SUPERSELF: self.superself,
            Brand.SMARTPARTS: self.smartparts,
            Brand.CLEOCORA: self.cleocora,
        }

    def _id_to_brand(self) -> dict[int, Brand]:
        return {v: k for k, v in self._brand_to_id().items()}


class AppSettings(BaseSettings):
    app_debug: bool = False
    init_ref_update: bool = False
    zendesk: ZendeskSettings = Field(default_factory=ZendeskSettings)
    rabbitmq: RabbitMQSettings = Field(default_factory=RabbitMQSettings)
    amazon: AmazonSettings = Field(default_factory=AmazonSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    brand: BrandSettings = Field(default_factory=BrandSettings)
    web: WebAdminSettings = Field(default_factory=WebAdminSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def load(cls) -> Self:
        return cls()


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    return AppSettings.load()
