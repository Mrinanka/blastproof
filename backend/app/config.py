"""Settings, read once from environment variables (and backend/.env locally)."""

import json
from collections.abc import Callable, Mapping
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    PrivateAttr,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.security import MIN_SECRET_BYTES
from app.services.llm import MODEL_STRING, RESERVED_OPTION_KEYS, LlmPrice

DEFAULT_POLICY_FILE = Path(__file__).resolve().parent.parent / "policies" / "rules.yaml"

DEFAULT_EMBEDDING_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "fastembed"

DEFAULT_SUMMARY_MODEL = "google_genai:gemini-3.8-flash"
# Gemini 3.8 Flash list price through 2026-12-31; it doubles on 2027-01-01 (see PROGRESS.md).
DEFAULT_LLM_PRICES = {
    DEFAULT_SUMMARY_MODEL: LlmPrice(
        input_per_million=Decimal("0.75"), output_per_million=Decimal("3.75")
    )
}
# Settings production must take from its environment: the defaults above are for dev machines, and
# a deployed backend never runs on a model or price nobody chose (D-165). Field name -> env name.
SET_EXPLICITLY_IN_PRODUCTION = {
    "summary_model": "SUMMARY_MODEL",
    "judge_model": "JUDGE_MODEL",
    "llm_prices": "LLM_PRICES",
}
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _model_string(value: str, name: str) -> str:
    """`provider:model` or a clear error that never repeats the value (ARCH-19)."""
    if not MODEL_STRING.fullmatch(value):
        raise ValueError(
            f"{name} must be a provider:model string: LangChain's lowercase provider name, a colon,"
            " then the model (a bare model name would make LangChain guess the provider)"
        )
    return value


def _json_object(value: object, name: str) -> object:
    """Our own JSON parse (NoDecode), so an empty value is the default and a bad one says so
    without repeating the value, which could hold a key."""
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value, parse_float=Decimal)
    except ValueError:
        raise ValueError(f"{name} must be a JSON object") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


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
    # LangGraph's checkpointer, as blastproof_app (plain postgresql:// URL: psycopg 3, no +asyncpg).
    # On Neon: the direct endpoint and no channel_binding. Required when APP_ENV=production.
    checkpointer_database_url: SecretStr | None = None
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
    # The shadow executor's login (step 10): blastproof_writer, direct endpoint, no channel_binding.
    company_db_writer_url: SecretStr | None = None
    local_company_writer_url: SecretStr | None = None
    # A superuser on the Compose company-db that clones `company_main` for shadow runs. Tests and
    # CI only; production refuses it (SEC-17).
    fake_neon_admin_url: SecretStr | None = None
    neon_api_key: SecretStr | None = None
    neon_project_id: str | None = None
    neon_parent_branch_id: str | None = None
    # `api`: real Neon branches. `fake`: local `CREATE DATABASE ... TEMPLATE` clones (CI).
    neon_client: Literal["api", "fake"] = "api"

    # The write rules (step 9), read once at startup. Optional: tests point it at a bad file.
    policy_file: Path = DEFAULT_POLICY_FILE

    # Knowledge and RAG (step 11). The model is downloaded once into the cache folder (outside the
    # system temp folder, which can be wiped). Only pgvector exists until QdrantStore is built.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path = DEFAULT_EMBEDDING_CACHE_DIR
    vector_store: Literal["pgvector"] = "pgvector"

    # The impact summary (step 12): any `provider:model` string for init_chat_model (ARCH-19). The
    # provider's key (e.g. GOOGLE_API_KEY) is read by LangChain from the process environment.
    summary_model: str = DEFAULT_SUMMARY_MODEL
    # Extra constructor arguments as a JSON object, e.g. {"thinking_level": "low"}. Never logged
    # (repr=False): they could hold a key.
    summary_model_options: Annotated[dict[str, Any], NoDecode] = Field(
        default_factory=dict, repr=False
    )
    # Price per million tokens per model, as JSON: {"<model>": {"input_per_million": 0.75,
    # "output_per_million": 3.75}}. A set value replaces the default table.
    llm_prices: Annotated[dict[str, LlmPrice], NoDecode] = Field(
        default_factory=lambda: dict(DEFAULT_LLM_PRICES)
    )

    # The risk judge (step 13). JUDGE_PROVIDER picks the implementation (`llm` is the only one
    # until Jev); JUDGE_MODEL is any `provider:model` string, like SUMMARY_MODEL, and needs an
    # LLM_PRICES entry too.
    judge_provider: str = "llm"
    judge_model: str = DEFAULT_SUMMARY_MODEL
    judge_model_options: Annotated[dict[str, Any], NoDecode] = Field(
        default_factory=dict, repr=False
    )

    # Tunables from backend plan §11. URLs, secrets and providers come with the step that uses them.
    review_wait_seconds: int = 20
    status_max_wait_seconds: int = 50
    review_timeout_hours: int = 24
    agent_poll_budget_minutes: int = 10
    max_concurrent_shadows: int = 2
    shadow_timeout_seconds: int = 60
    llm_timeout_seconds: int = 20
    llm_max_tokens: int = 1024
    max_writes_per_minute_per_agent: int = 30
    max_llm_cost_per_action_usd: float = 0.10
    judge_min_confidence: float = 0.6
    retention_days: int = 30

    # Which of SET_EXPLICITLY_IN_PRODUCTION came with a non-empty value (`_remember_explicit`).
    _explicit: frozenset[str] = PrivateAttr(default_factory=frozenset)

    @model_validator(mode="wrap")
    @classmethod
    def _remember_explicit(
        cls, data: object, handler: Callable[[object], "Settings"]
    ) -> "Settings":
        # An empty value means the default (backend/.env holds empty placeholders), so it does not
        # count as set, although pydantic lists it in `model_fields_set`.
        settings = handler(data)
        if isinstance(data, Mapping):
            settings._explicit = frozenset(
                name for name in SET_EXPLICITLY_IN_PRODUCTION if data.get(name) not in (None, "")
            )
        return settings

    @property
    def explicit_fields(self) -> frozenset[str]:
        return self._explicit

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "blastproof_database_url",
        "checkpointer_database_url",
        "jwt_secret",
        "company_db_reader_url",
        "local_company_reader_url",
        "company_db_writer_url",
        "local_company_writer_url",
        "fake_neon_admin_url",
        "neon_api_key",
        "neon_project_id",
        "neon_parent_branch_id",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # backend/.env holds empty placeholders until the owner fills them in.
        return None if value == "" else value

    @field_validator(
        "public_base_url",
        "company_connection_profile",
        "neon_client",
        "embedding_model",
        "embedding_cache_dir",
        "vector_store",
        "summary_model",
        "judge_provider",
        "judge_model",
        mode="before",
    )
    @classmethod
    def _empty_is_default(cls, value: object, info: ValidationInfo) -> object:
        assert info.field_name is not None
        return cls.model_fields[info.field_name].default if value == "" else value

    @field_validator("summary_model_options", "judge_model_options", mode="before")
    @classmethod
    def _parse_options(cls, value: object, info: ValidationInfo) -> object:
        assert info.field_name is not None
        return {} if value == "" else _json_object(value, info.field_name.upper())

    @field_validator("llm_prices", mode="before")
    @classmethod
    def _parse_prices(cls, value: object) -> object:
        return dict(DEFAULT_LLM_PRICES) if value == "" else _json_object(value, "LLM_PRICES")

    @field_validator("summary_model", "judge_model")
    @classmethod
    def _provider_model(cls, value: str, info: ValidationInfo) -> str:
        assert info.field_name is not None
        return _model_string(value, info.field_name.upper())

    @field_validator("llm_prices")
    @classmethod
    def _priced_models_are_provider_model(cls, value: dict[str, LlmPrice]) -> dict[str, LlmPrice]:
        # A bare key would never match a model setting, which must be `provider:model`.
        for model in value:
            _model_string(model, "LLM_PRICES keys")
        return value

    @field_validator("summary_model_options", "judge_model_options")
    @classmethod
    def _no_reserved_options(cls, value: dict[str, Any], info: ValidationInfo) -> dict[str, Any]:
        assert info.field_name is not None
        reserved = sorted(RESERVED_OPTION_KEYS & set(value))
        if reserved:
            raise ValueError(
                f"{info.field_name.upper()} may not set {reserved}: Blastproof sets these itself"
            )
        return value

    @field_validator("public_base_url")
    @classmethod
    def _public_base_url_is_an_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("PUBLIC_BASE_URL must be an http:// or https:// URL with a host")
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("PUBLIC_BASE_URL must be scheme and host only, e.g. https://x.fly.dev")
        return value.rstrip("/")

    @field_validator(
        "company_db_reader_url",
        "local_company_reader_url",
        "company_db_writer_url",
        "local_company_writer_url",
    )
    @classmethod
    def _no_channel_binding(cls, value: SecretStr | None) -> SecretStr | None:
        # asyncpg passes unknown URL parameters to Postgres as settings, and the connection fails.
        if value is not None and "channel_binding" in value.get_secret_value().lower():
            raise ValueError("Remove channel_binding from the company database URL")
        return value

    @field_validator("checkpointer_database_url")
    @classmethod
    def _checkpointer_url_is_plain(cls, value: SecretStr | None) -> SecretStr | None:
        # psycopg 3 reads postgresql:// URLs; "+asyncpg" is SQLAlchemy's driver suffix. The value is
        # never repeated in the error.
        if value is not None:
            scheme = urlsplit(value.get_secret_value()).scheme
            if scheme not in ("postgresql", "postgres"):
                raise ValueError(
                    "CHECKPOINTER_DATABASE_URL must be a plain postgresql:// URL (no +asyncpg)"
                )
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
