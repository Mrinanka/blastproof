"""Gate audit (ARCH-19, D-165): model settings name their provider, and production sets them itself.

A bare model name would let `init_chat_model` guess the provider (a bare `gemini-...` becomes Vertex
AI, not `google_genai`), so SUMMARY_MODEL, JUDGE_MODEL and every LLM_PRICES key must be
`provider:model`. Production must also set the model and price settings explicitly: a code default
is for dev machines only.
"""

import pytest
from pydantic import ValidationError

import app.main as main
from app.config import DEFAULT_LLM_PRICES, DEFAULT_SUMMARY_MODEL, Settings
from tests.conftest import TEST_JWT_SECRET

BAD_MODELS = [
    "gemini-3.8-flash",  # bare: LangChain would guess the provider
    ":gemini-3.8-flash",
    "google_genai:",
    "google genai:gemini-3.8-flash",
    "google_genai: gemini-3.8-flash",
    "Google_GenAI:gemini-3.8-flash",  # LangChain's provider names are lowercase
    "1provider:model",
]
GOOD_MODELS = [
    "google_genai:gemini-3.8-flash",
    "anthropic:claude-haiku-5-5",
    "ollama:llama3:8b",  # only the first colon splits
    "bedrock_converse:anthropic.claude-v2:1",
]
FIELDS = [("summary_model", "SUMMARY_MODEL"), ("judge_model", "JUDGE_MODEL")]
PRICE = {"input_per_million": 1, "output_per_million": 5}


@pytest.mark.parametrize(("field", "env"), FIELDS)
@pytest.mark.parametrize("value", BAD_MODELS)
def test_a_model_without_a_provider_is_refused(field: str, env: str, value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, app_env="test", **{field: value})
    message = str(caught.value)
    assert env in message and "provider:model" in message
    assert value not in message


@pytest.mark.parametrize(("field", "env"), FIELDS)
def test_a_bare_model_from_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch, field: str, env: str
) -> None:
    monkeypatch.setenv(env, "gemini-3.8-flash")
    with pytest.raises(ValidationError, match=env):
        Settings(_env_file=None, app_env="test")


@pytest.mark.parametrize("value", GOOD_MODELS)
def test_provider_model_strings_are_accepted(value: str) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        summary_model=value,
        judge_model=value,
        llm_prices={value: PRICE},
    )
    assert settings.summary_model == settings.judge_model == value


@pytest.mark.parametrize("value", BAD_MODELS)
def test_a_price_for_a_bare_model_is_refused(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, app_env="test", llm_prices={value: PRICE})
    message = str(caught.value)
    assert "LLM_PRICES" in message and "provider:model" in message
    assert value not in message


def test_the_code_defaults_pass_their_own_check() -> None:
    settings = Settings(_env_file=None, app_env="test")
    assert settings.summary_model == settings.judge_model == DEFAULT_SUMMARY_MODEL
    assert settings.llm_prices == DEFAULT_LLM_PRICES


# --- Production sets the model settings itself (D-165) --------------------------------------

PRODUCTION = {
    "app_env": "production",
    "neon_client": "api",  # CI sets NEON_CLIENT=fake
    "blastproof_database_url": "postgresql+asyncpg://blastproof_app:x@db.invalid/blastproof",
    "checkpointer_database_url": "postgresql://blastproof_app:x@db.invalid/blastproof",
    "jwt_secret": TEST_JWT_SECRET,
    "company_db_reader_url": "postgresql://blastproof_reader:x@db.invalid/neondb",
    "company_db_writer_url": "postgresql://blastproof_writer:x@db.invalid/neondb",
    "neon_api_key": "napi-key",
    "neon_project_id": "project",
    "neon_parent_branch_id": "br-parent",
}
MODELS = {
    "summary_model": "anthropic:claude-haiku-5-5",
    "judge_model": "anthropic:claude-haiku-5-5",
    "llm_prices": {"anthropic:claude-haiku-5-5": PRICE},
}
LLM_ENV = {
    "summary_model": "SUMMARY_MODEL",
    "judge_model": "JUDGE_MODEL",
    "llm_prices": "LLM_PRICES",
}


def test_production_with_explicit_model_settings_builds() -> None:
    app = main.create_app(Settings(_env_file=None, **(PRODUCTION | MODELS)))  # type: ignore[arg-type]
    assert app.state.judge is not None


def test_production_refuses_to_run_on_the_code_defaults() -> None:
    with pytest.raises(main.MissingProductionSettingError) as caught:
        main.create_app(Settings(_env_file=None, **PRODUCTION))  # type: ignore[arg-type]
    for env in LLM_ENV.values():
        assert env in str(caught.value)


@pytest.mark.parametrize("field", sorted(MODELS))
def test_production_needs_each_model_setting(field: str) -> None:
    values = {k: v for k, v in MODELS.items() if k != field}
    with pytest.raises(main.MissingProductionSettingError, match=LLM_ENV[field]):
        main.create_app(Settings(_env_file=None, **(PRODUCTION | values)))  # type: ignore[arg-type]


def test_production_treats_empty_values_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """backend/.env holds the names with empty values; empty means the code default."""
    for env in LLM_ENV.values():
        monkeypatch.setenv(env, "")
    with pytest.raises(main.MissingProductionSettingError) as caught:
        main.create_app(Settings(_env_file=None, **PRODUCTION))  # type: ignore[arg-type]
    for env in LLM_ENV.values():
        assert env in str(caught.value)


def test_production_accepts_model_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUMMARY_MODEL", "anthropic:claude-haiku-5-5")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic:claude-haiku-5-5")
    monkeypatch.setenv(
        "LLM_PRICES",
        '{"anthropic:claude-haiku-5-5": {"input_per_million": 1, "output_per_million": 5}}',
    )
    main.create_app(Settings(_env_file=None, **PRODUCTION))  # type: ignore[arg-type]


def test_production_accepts_the_default_model_when_it_is_set_explicitly() -> None:
    """Explicit, not different: choosing the default model on purpose is fine."""
    explicit = {
        "summary_model": DEFAULT_SUMMARY_MODEL,
        "judge_model": DEFAULT_SUMMARY_MODEL,
        "llm_prices": {DEFAULT_SUMMARY_MODEL: PRICE},
    }
    main.create_app(Settings(_env_file=None, **(PRODUCTION | explicit)))  # type: ignore[arg-type]


def test_explicit_settings_survive_model_copy() -> None:
    """test_db_session builds variants with model_copy; the explicit set must carry over."""
    settings = Settings(_env_file=None, **(PRODUCTION | MODELS))  # type: ignore[arg-type]
    main.create_app(settings.model_copy(update={"log_level": "INFO"}))


def test_outside_production_the_defaults_still_apply() -> None:
    main.create_app(Settings(_env_file=None, app_env="local"))
