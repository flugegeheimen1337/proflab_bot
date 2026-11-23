from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    bot_username: str = ""
    database_url: str = "postgresql+asyncpg://anon:change-me@postgres:5432/anonchat"
    redis_url: str = "redis://redis:6379/0"
    admin_ids: str = ""
    default_group_id: int | None = None
    timezone: str = "Europe/Moscow"
    ephemeral_mode_enabled: bool = False
    intercept_mode_enabled: bool = False
    reveal_telegram_ids: bool = False
    log_level: str = "INFO"
    callback_secret: SecretStr
    admin_api_token: SecretStr = SecretStr("")
    messages_per_minute: int = 5
    max_message_length: int = 1000
    allowed_types: str = "text,photo,video,document"
    spam_block_seconds: int = 600
    max_queue_per_user: int = 10
    group_send_interval_ms: int = 1100
    group_send_per_minute: int = 20
    queue_alert_depth: int = 30
    max_media_bytes: int = 15 * 1024 * 1024
    health_port: int = 8081
    api_port: int = 8080

    @field_validator("default_group_id", mode="before")
    @classmethod
    def _empty_group(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @property
    def admin_id_set(self) -> frozenset[int]:
        values: set[int] = set()
        for part in self.admin_ids.split(","):
            part = part.strip()
            if part:
                values.add(int(part))
        return frozenset(values)

    def secret(self) -> str:
        return self.callback_secret.get_secret_value()

    def token(self) -> str:
        return self.bot_token.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
