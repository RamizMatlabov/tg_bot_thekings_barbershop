"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the notification service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., min_length=1)
    telegram_chat_id: str = Field(..., min_length=1)
    webhook_secret: str = Field(..., min_length=8)
    database_url: str = "sqlite:///./barbershop.db"
    timezone: str = "Asia/Tashkent"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Request / reliability tuning
    max_request_body_bytes: int = 65_536
    notification_max_attempts: int = 5
    notification_base_backoff_seconds: float = 2.0
    notification_worker_poll_interval_seconds: float = 2.0
    notification_batch_size: int = 20

    @field_validator("telegram_bot_token", "telegram_chat_id", "webhook_secret", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def sqlite_path(self) -> str:
        """Resolve a filesystem path from a sqlite DATABASE_URL."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url.removeprefix("sqlite:///")
        if url.startswith("sqlite://"):
            return url.removeprefix("sqlite://")
        return url


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
