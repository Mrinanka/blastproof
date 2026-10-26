"""Step 13 (BE-71, ARCH-18, ARCH-19, DW-B13 part 4): JUDGE_PROVIDER, JUDGE_MODEL and
JUDGE_MODEL_OPTIONS load safely, and switching the provider needs no other change."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_SUMMARY_MODEL, Settings
from app.db.enums import Decision
from app.main import create_app
from app.services import judge as judge_module
from app.services.judge import (
    JUDGE_PROVIDERS,
    JudgeConfigError,
    JudgeContext,
    JudgeResult,
    LLMJudge,
    assess_safely,
    build_judge,
    decide,
)
from app.services.llm import RESERVED_OPTION_KEYS, LlmConfigError
from tests.conftest import NetworkGuard
from tests.impact_helpers import POLICY, FakeFactory
from tests.judge_helpers import (
    FakeJudgeLlm,
    replit_context,
    result_of,
    rule_decision,
    verdict,
)

NAMES = ("JUDGE_PROVIDER", "JUDGE_MODEL", "JUDGE_MODEL_OPTIONS")


def test_the_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.judge_provider == "llm"
    assert settings.judge_model == DEFAULT_SUMMARY_MODEL == "google_genai:gemini-3.8-flash"
    assert settings.judge_model_options == {}
    assert settings.judge_min_confidence == 0.6


def test_empty_environment_values_give_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.setenv(name, "")
    settings = Settings(_env_file=None)
    assert settings.judge_provider == "llm"
    assert settings.judge_model == DEFAULT_SUMMARY_MODEL
    assert settings.judge_model_options == {}


def test_empty_lines_in_an_env_file_give_the_defaults(tmp_path: Path) -> None:
    """backend/.env holds these three names with empty values."""
    env = tmp_path / ".env"
    env.write_text("".join(f"{name}=\n" for name in NAMES), encoding="utf-8")
    settings = Settings(_env_file=env)
    assert (settings.judge_provider, settings.judge_model) == ("llm", DEFAULT_SUMMARY_MODEL)
    assert settings.judge_model_options == {}


def test_env_example_parses_with_its_quotes() -> None:
    from tests.conftest import BACKEND_DIR

    settings = Settings(_env_file=BACKEND_DIR / ".env.example", app_env="test")
    assert settings.judge_model_options == {"thinking_level": "low"}
    assert settings.judge_provider == "llm" and settings.judge_model == DEFAULT_SUMMARY_MODEL


def test_values_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE_PROVIDER", "other")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic:claude-haiku-5-5")
    monkeypatch.setenv("JUDGE_MODEL_OPTIONS", '{"thinking_level": "low"}')
    settings = Settings(_env_file=None)
    assert settings.judge_provider == "other"
    assert settings.judge_model == "anthropic:claude-haiku-5-5"
    assert settings.judge_model_options == {"thinking_level": "low"}


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
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("JUDGE_MODEL_OPTIONS", value)
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)
    message = str(raised.value)
    assert "JUDGE_MODEL_OPTIONS must be a JSON object" in message
    assert "AIza-leak-value" not in message


@pytest.mark.parametrize("key", sorted(RESERVED_OPTION_KEYS))
def test_reserved_option_keys_are_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="JUDGE_MODEL_OPTIONS may not set"):
        Settings(_env_file=None, judge_model_options={key: 1})


def test_the_summary_options_error_still_names_its_own_setting() -> None:
    with pytest.raises(ValidationError, match="SUMMARY_MODEL_OPTIONS may not set"):
        Settings(_env_file=None, summary_model_options={"model": "x"})


def test_options_are_left_out_of_the_settings_repr() -> None:
    settings = Settings(_env_file=None, judge_model_options={"api_key": "AIza-repr-secret"})
    assert "AIza-repr-secret" not in repr(settings)


def test_a_model_without_a_price_stops_create_app() -> None:
    settings = Settings(_env_file=None, app_env="test", judge_model="anthropic:claude-haiku-5-5")
    with pytest.raises(LlmConfigError, match="anthropic:claude-haiku-5-5"):
        create_app(settings)


def test_an_unknown_judge_provider_stops_create_app() -> None:
    settings = Settings(_env_file=None, app_env="test", judge_provider="nonsense")
    with pytest.raises(JudgeConfigError, match="JUDGE_PROVIDER names no judge; use one of"):
        create_app(settings)


def test_the_unknown_provider_error_does_not_repeat_the_value() -> None:
    with pytest.raises(JudgeConfigError) as raised:
        build_judge(Settings(_env_file=None, judge_provider="AIza-leak-value"), POLICY)
    assert "AIza-leak-value" not in str(raised.value)


def test_create_app_builds_the_judge_offline(settings: Settings, no_network: NetworkGuard) -> None:
    app = create_app(settings)
    assert isinstance(app.state.judge, LLMJudge)
    assert app.state.judge._model._value is None  # nothing built until the first assessment


def test_a_price_for_another_model_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        summary_model="anthropic:claude-haiku-5-5",
        judge_model="anthropic:claude-haiku-5-5",
        llm_prices={
            "anthropic:claude-haiku-5-5": {"input_per_million": 1, "output_per_million": 5}
        },
    )
    assert isinstance(create_app(settings).state.judge, LLMJudge)


async def test_any_provider_model_string_is_passed_to_the_factory_as_it_is() -> None:
    """ARCH-19: the provider comes only from the `provider:model` string, never from our code."""
    model = "anthropic:claude-haiku-5-5"
    seen: list[str] = []
    llm = FakeJudgeLlm(parsed=verdict())

    def factory(name: str, options: Any, settings: Settings) -> Any:
        seen.append(name)
        return FakeFactory(llm)(name, options, settings)

    judge = LLMJudge(
        Settings(
            _env_file=None,
            app_env="test",
            judge_model=model,
            llm_prices={model: {"input_per_million": 1, "output_per_million": 5}},
        ),
        POLICY,
        model_factory=factory,
    )
    result = await judge.assess(replit_context())
    assert seen == [model] and result.model == model and result.failure is None


# --- The provider switch (DW-B13 part 4) ---------------------------------------------------


class FixedJudge:
    """A second provider that always recommends `block`; the workflow code is unchanged."""

    provider = "fixed"

    def __init__(self, settings: Settings, policy: object) -> None:
        self.settings = settings

    async def assess(self, context: JudgeContext) -> JudgeResult:
        return result_of("block", risk_level="critical")


async def test_switching_judge_provider_needs_no_other_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(JUDGE_PROVIDERS, "fixed", FixedJudge)
    default = build_judge(Settings(_env_file=None), POLICY)
    assert isinstance(default, LLMJudge)

    judge = build_judge(Settings(_env_file=None, judge_provider="fixed"), POLICY)
    assert isinstance(judge, FixedJudge) and judge.provider == "fixed"
    result = await assess_safely(judge, replit_context(), 1)
    final = decide(rule_decision(Decision.ALLOW), result, [], min_confidence=0.6)
    assert final.decision == Decision.BLOCK and final.risk_level == "critical"

    app = create_app(Settings(_env_file=None, app_env="test", judge_provider="fixed"))
    assert isinstance(app.state.judge, FixedJudge)


def test_the_registry_holds_the_llm_judge() -> None:
    assert JUDGE_PROVIDERS["llm"] is LLMJudge
    assert judge_module.LLMJudge.provider == "llm"
