"""Settings, read once from environment variables (and backend/.env locally)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.security import MIN_SECRET_BYTES

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    # extra="ignore": .env holds keys for later steps that are not declared here yet.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["local", "test", "production"] = "local"
    log_level: LogLevel = "INFO"

    # Migrations only (blastproof_owner, plain postgresql:// URL); never used by the running app.
    # SecretStr keeps the password out of repr() and logs.
    blastproof_migrator_url: SecretStr | None = None

    # The running app, as blastproof_app (postgresql+asyncpg://...). On Neon: the direct endpoint
    # and no channel_binding parameter (asyncpg would send it to Postgres as a setting).
    blastproof_database_url: SecretStr | None = None
    # Signs dashboard tokens (HS256): at least 32 bytes. Required when APP_ENV=production.
    jwt_secret: SecretStr | None = None

    # Tunables from backend plan §11. URLs, secrets and providers come with the step that uses them.
    review_wait_seconds: int = 20
    status_max_wait_seconds: int = 50
    review_timeout_hours: int = 24
    agent_poll_budget_minutes: int = 10
    max_concurrent_shadows: int = 2
    llm_timeout_seconds: int = 20
    llm_max_tokens: int = 1024
    max_writes_per_minute_per_agent: int = 30
    max_llm_cost_per_action_usd: float = 0.10
    judge_min_confidence: float = 0.6
    retention_days: int = 30

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("blastproof_database_url", "jwt_secret", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # backend/.env holds empty placeholders until the owner fills them in.
        return None if value == "" else value

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
