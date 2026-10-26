"""Step 12 (ARCH-19): the real chat model is built from the config string with our limits.

Constructs the real LangChain class with a dummy key and no network, so a keyword the class would
silently ignore shows up as a missing value here.
"""

from decimal import Decimal

import pytest

from app.config import DEFAULT_SUMMARY_MODEL, Settings
from app.services.impact import ImpactReport
from app.services.llm import (
    LazyModel,
    LlmConfigError,
    LlmPrice,
    build_chat_model,
    cost_usd,
    price_for,
)
from tests.conftest import NetworkGuard


def build(**options: object) -> object:
    settings = Settings(_env_file=None, app_env="test", llm_timeout_seconds=17, llm_max_tokens=900)
    return build_chat_model(DEFAULT_SUMMARY_MODEL, {"api_key": "dummy", **options}, settings)


def test_the_real_gemini_class_gets_every_limit(no_network: NetworkGuard) -> None:
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = build(thinking_level="low")
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model == "gemini-3.8-flash"
    assert model.max_output_tokens == 900
    assert model.timeout == 17
    assert model.max_retries == 1
    assert model.thinking_level == "low"
    # Never passed: Gemini 3.6 and later deprecate custom sampling.
    assert model.temperature is None


def test_structured_output_with_the_raw_message_builds(no_network: NetworkGuard) -> None:
    structured = build().with_structured_output(ImpactReport, include_raw=True)  # type: ignore[attr-defined]
    assert hasattr(structured, "ainvoke")


def test_the_provider_comes_only_from_the_string(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain.chat_models

    seen: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        langchain.chat_models, "init_chat_model", lambda *a, **kw: seen.append((a, kw))
    )
    settings = Settings(_env_file=None, app_env="test")
    build_chat_model("anthropic:claude-haiku-5-5", {"thinking_level": "low"}, settings)
    [(args, kwargs)] = seen
    assert args == ("anthropic:claude-haiku-5-5",)
    assert kwargs == {"timeout": 20, "max_tokens": 1024, "max_retries": 1, "thinking_level": "low"}


def test_an_unknown_provider_fails_to_build() -> None:
    settings = Settings(_env_file=None, app_env="test")
    with pytest.raises(ValueError):
        build_chat_model("nosuchprovider:model-x", {}, settings)


def test_cost_is_tokens_times_price_in_decimal() -> None:
    price = LlmPrice(input_per_million=Decimal("0.75"), output_per_million=Decimal("3.75"))
    assert cost_usd(price, 1_200, 300) == Decimal("0.002025")
    assert cost_usd(price, 0, 0) == Decimal("0.000000")
    # 1 input token is 0.00000075: rounded half up at 6 decimal places.
    assert cost_usd(price, 1, 0) == Decimal("0.000001")
    assert cost_usd(price, 15_000, 1_024) == Decimal("0.015090")


def test_price_for_refuses_an_unknown_model() -> None:
    with pytest.raises(LlmConfigError, match="'x:y'"):
        price_for("x:y", {})


async def test_lazy_model_retries_after_a_failed_build_and_then_caches() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("no key")
        return "model"

    lazy = LazyModel(flaky)
    with pytest.raises(RuntimeError):
        await lazy.get()
    assert await lazy.get() == "model"
    assert await lazy.get() == "model"
    assert calls["n"] == 2
