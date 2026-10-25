"""Settings, read once from environment variables (and backend/.env locally)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.security import MIN_SECRET_BYTES

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    # extra="ignore": .env holds keys for later steps that are not declared here yet.
    # hide_input_in_errors: a rejected URL or secret must not appear in the startup error.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )

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

    # The backend's public address. Its host is allowed by the MCP DNS-rebinding check (D-099),
    # and step 19 builds agents' `mcp_url` from it.
    public_base_url: str = "http://localhost:8000"

    # Which company database the one "main" system uses: `neon` (COMPANY_DB_READER_URL, the
    # default and the only choice in production) or `local` (the Compose company-db).
    company_connection_profile: Literal["neon", "local"] = "neon"
    # Plain postgresql:// URLs for the read-only blastproof_reader role. On Neon: the direct
    # endpoint, sslmode=require and no channel_binding (asyncpg would send it as a setting).
    company_db_reader_url: SecretStr | None = None
    local_company_reader_url: SecretStr | None = None
    # Stored on the system row; the Neon client itself comes in step 10.
    neon_project_id: str | None = None
    neon_parent_branch_id: str | None = None

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

    @field_validator(
        "blastproof_database_url",
        "jwt_secret",
        "company_db_reader_url",
        "local_company_reader_url",
        "neon_project_id",
        "neon_parent_branch_id",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # backend/.env holds empty placeholders until the owner fills them in.
        return None if value == "" else value

    @field_validator("public_base_url", "company_connection_profile", mode="before")
    @classmethod
    def _empty_is_default(cls, value: object, info: ValidationInfo) -> object:
        assert info.field_name is not None
        return cls.model_fields[info.field_name].default if value == "" else value

    @field_validator("public_base_url")
    @classmethod
    def _public_base_url_is_an_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("PUBLIC_BASE_URL must be an http:// or https:// URL with a host")
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("PUBLIC_BASE_URL must be scheme and host only, e.g. https://x.fly.dev")
        return value.rstrip("/")

    @field_validator("company_db_reader_url", "local_company_reader_url")
    @classmethod
    def _no_channel_binding(cls, value: SecretStr | None) -> SecretStr | None:
        # asyncpg passes unknown URL parameters to Postgres as settings, and the connection fails.
        if value is not None and "channel_binding" in value.get_secret_value().lower():
            raise ValueError("Remove channel_binding from the company database URL")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        return value

    @property
    def public_host(self) -> str:
        """The Host header clients send for PUBLIC_BASE_URL: no port when it is the default one."""
        parts = urlsplit(self.public_base_url)
        default_port = 443 if parts.scheme == "https" else 80
        return parts.netloc if parts.port not in (None, default_port) else parts.hostname or ""

    @property
    def public_origin(self) -> str:
        return f"{urlsplit(self.public_base_url).scheme}://{self.public_host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
