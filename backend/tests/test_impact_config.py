"""Step 12: SUMMARY_MODEL, SUMMARY_MODEL_OPTIONS and LLM_PRICES load safely or stop startup."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_LLM_PRICES, DEFAULT_SUMMARY_MODEL, Settings
from app.main import create_app
from app.services.impact import ImpactEngine
from app.services.llm import RESERVED_OPTION_KEYS, LlmConfigError, LlmPrice
from tests.conftest import NetworkGuard

NAMES = ("SUMMARY_MODEL", "SUMMARY_MODEL_OPTIONS", "LLM_PRICES")


def test_the_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.summary_model == DEFAULT_SUMMARY_MODEL == "google_genai:gemini-3.8-flash"
    assert settings.summary_model_options == {}
    assert settings.llm_prices == DEFAULT_LLM_PRICES
    assert settings.llm_prices[DEFAULT_SUMMARY_MODEL] == LlmPrice(
        input_per_million=Decimal("0.75"), output_per_million=Decimal("3.75")
    )


def test_empty_environment_values_give_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.setenv(name, "")
    settings = Settings(_env_file=None)
    assert settings.summary_model == DEFAULT_SUMMARY_MODEL
    assert settings.summary_model_options == {}
    assert settings.llm_prices == DEFAULT_LLM_PRICES


def test_empty_lines_in_an_env_file_give_the_defaults(tmp_path: Path) -> None:
    """backend/.env holds these three names with empty values."""
    env = tmp_path / ".env"
    env.write_text("".join(f"{name}=\n" for name in NAMES), encoding="utf-8")
    settings = Settings(_env_file=env)
    assert settings.summary_model == DEFAULT_SUMMARY_MODEL
    assert settings.summary_model_options == {}
    assert settings.llm_prices == DEFAULT_LLM_PRICES


def test_single_quoted_json_in_an_env_file_is_read(tmp_path: Path) -> None:
    """.env.example quotes the JSON values, because `uv run --env-file` cannot parse them bare."""
    env = tmp_path / ".env"
    env.write_text(
        'SUMMARY_MODEL_OPTIONS=\'{"thinking_level": "low"}\'\n'
        'LLM_PRICES=\'{"google_genai:gemini-3.8-flash":'
        ' {"input_per_million": 0.75, "output_per_million": 3.75}}\'\n',
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    assert settings.summary_model_options == {"thinking_level": "low"}
    assert settings.llm_prices == DEFAULT_LLM_PRICES


def test_env_example_parses_with_its_quotes() -> None:
    from tests.conftest import BACKEND_DIR

    settings = Settings(_env_file=BACKEND_DIR / ".env.example", app_env="test")
    assert settings.summary_model_options == {"thinking_level": "low"}


def test_values_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARY_MODEL", "anthropic:claude-haiku-5-5")
    monkeypatch.setenv("SUMMARY_MODEL_OPTIONS", '{"thinking_level": "low"}')
    monkeypatch.setenv(
        "LLM_PRICES",
        '{"anthropic:claude-haiku-5-5": {"input_per_million": 0.1, "output_per_million": 0.3}}',
    )
    settings = Settings(_env_file=None)
    assert settings.summary_model_options == {"thinking_level": "low"}
    price = settings.llm_prices["anthropic:claude-haiku-5-5"]
    assert price.input_per_million == Decimal("0.1")  # exact: parsed as Decimal, never a float
    assert price.output_per_million == Decimal("0.3")
    assert isinstance(price.input_per_million, Decimal)


@pytest.mark.parametrize("name", ["SUMMARY_MODEL_OPTIONS", "LLM_PRICES"])
@pytest.mark.parametrize(
    "value",
    [
        '{"api_key": "AIza-leak-value"',
        '["AIza-leak-value"]',
        '"AIza-leak-value"',
        "AIza-leak-value",
    ],
)
def test_bad_json_is_a_clear_error_that_hides_the_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)
    message = str(raised.value)
    assert f"{name} must be a JSON object" in message
    assert "AIza-leak-value" not in message


@pytest.mark.parametrize("key", sorted(RESERVED_OPTION_KEYS))
def test_reserved_option_keys_are_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="may not set"):
        Settings(_env_file=None, summary_model_options={key: 1})


@pytest.mark.parametrize(
    "price",
    [
        {"input_per_million": -1, "output_per_million": 1},
        {"input_per_million": 1, "output_per_million": "-0.01"},
        {"input_per_million": 1},
        {"input_per_million": 1, "output_per_million": 1, "cached_per_million": 1},
    ],
)
def test_a_bad_price_is_refused(price: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_prices={"m:x": price})


def test_options_are_left_out_of_the_settings_repr() -> None:
    settings = Settings(_env_file=None, summary_model_options={"api_key": "AIza-repr-secret"})
    assert "AIza-repr-secret" not in repr(settings)


def test_a_model_without_a_price_stops_create_app() -> None:
    settings = Settings(_env_file=None, app_env="test", summary_model="anthropic:claude-haiku-5-5")
    with pytest.raises(LlmConfigError, match="anthropic:claude-haiku-5-5"):
        create_app(settings)


def test_a_price_for_another_model_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        summary_model="anthropic:claude-haiku-5-5",
        judge_model="anthropic:claude-haiku-5-5",  # the judge needs a price too (step 13)
        llm_prices={
            "anthropic:claude-haiku-5-5": {"input_per_million": 1, "output_per_million": 5}
        },
    )
    assert isinstance(create_app(settings).state.impact, ImpactEngine)


def test_create_app_builds_the_engine_offline(settings: Settings, no_network: NetworkGuard) -> None:
    app = create_app(settings)
    assert isinstance(app.state.impact, ImpactEngine)
    assert app.state.impact._model._value is None  # nothing built until the first summary
