"""Settings, read once from environment variables (and backend/.env locally)."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    # extra="ignore": .env holds keys for later steps that are not declared here yet.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["local", "test", "production"] = "local"
    log_level: LogLevel = "INFO"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
