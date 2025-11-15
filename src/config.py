from contextvars import ContextVar
from typing import Self

from pydantic import BaseModel, EmailStr, SecretStr
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


class AppSettings(BaseSettings):
    zendesk: ZendeskSettings
    rabbitmq: RabbitMQSettings
    amazon: AmazonSettings
    telegram: TelegramSettings
    postgres: PostgresSettings

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
    )

    @classmethod
    def load(cls) -> Self:
        return cls()
