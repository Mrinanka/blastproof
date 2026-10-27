"""The scripted models behind `seed-demo-actions`: they pick a script by action_id, return token
usage, and their wording always passes the real engines' checks (no network, no Docker)."""

import re
import uuid
from decimal import Decimal

from app.config import Settings
from app.services.demo_seed import ADD_PENDING, SCENARIOS
from app.services.impact import ImpactEngine, ImpactReport, shape_ok
from app.services.judge import JudgeVerdict, verdict_ok
from app.services.llm import usage_tokens
from app.services.scripted_models import (
    DEFAULT_REPORT,
    DEFAULT_VERDICT,
    FAKE_INPUT_TOKENS,
    FAKE_OUTPUT_TOKENS,
    ScriptedModelFactory,
)
from tests.impact_helpers import POLICY, REPLIT_INTENT, REPLIT_SQL, replit_diff
from tests.test_impact_engine import classified

SETTINGS = Settings(_env_file=None, app_env="test")
NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|hundred|thousand|million|"
    r"half|dozen|once|twice|first|second|third)\b",
    re.IGNORECASE,
)


def structured(factory: ScriptedModelFactory, schema: type):  # type: ignore[no-untyped-def]
    return factory("provider:model", {}, SETTINGS).with_structured_output(schema, include_raw=True)


def config_for(action_id: object) -> dict:
    return {"metadata": {"action_id": str(action_id)}}


def all_wording() -> list[str]:
    texts: list[str] = []
    for scenario in (*SCENARIOS, ADD_PENDING):
        if scenario.summary:
            texts += [scenario.summary.headline, *scenario.summary.key_points]
            texts += scenario.summary.concerns
        if scenario.verdict:
            texts.append(scenario.verdict.reason)
    texts += [DEFAULT_REPORT.headline, *DEFAULT_REPORT.key_points, *DEFAULT_REPORT.concerns]
    return [*texts, DEFAULT_VERDICT.reason]


async def test_the_script_is_picked_by_action_id() -> None:
    factory = ScriptedModelFactory()
    first, second = uuid.uuid4(), uuid.uuid4()
    one = ImpactReport(headline="First wording.", key_points=[], concerns=[])
    two = ImpactReport(headline="Second wording.", key_points=[], concerns=[])
    factory.script(first, report=one)
    factory.script(second, report=two)
    runnable = structured(factory, ImpactReport)
    assert (await runnable.ainvoke([], config=config_for(first)))["parsed"] == one
    assert (await runnable.ainvoke([], config=config_for(second)))["parsed"] == two


async def test_an_unknown_action_gets_the_default_for_its_schema() -> None:
    factory = ScriptedModelFactory()
    unknown = config_for(uuid.uuid4())
    assert (await structured(factory, ImpactReport).ainvoke([], config=unknown))[
        "parsed"
    ] == DEFAULT_REPORT
    assert (await structured(factory, JudgeVerdict).ainvoke([], config=unknown))[
        "parsed"
    ] == DEFAULT_VERDICT
    assert (await structured(factory, ImpactReport).ainvoke([]))["parsed"] == DEFAULT_REPORT


async def test_the_report_and_the_verdict_scripts_do_not_mix() -> None:
    factory = ScriptedModelFactory()
    action_id = uuid.uuid4()
    verdict = JudgeVerdict(recommendation="allow", risk_level="low", confidence=0.9, reason="Fine.")
    factory.script(action_id, verdict=verdict)
    config = config_for(action_id)
    assert (await structured(factory, JudgeVerdict).ainvoke([], config=config))["parsed"] == verdict
    assert (await structured(factory, ImpactReport).ainvoke([], config=config))[
        "parsed"
    ] == DEFAULT_REPORT


async def test_the_usage_is_returned_with_no_parsing_error() -> None:
    answer = await structured(ScriptedModelFactory(), ImpactReport).ainvoke([])
    assert usage_tokens(answer["raw"]) == (FAKE_INPUT_TOKENS, FAKE_OUTPUT_TOKENS)
    assert answer["parsing_error"] is None


def test_only_the_two_known_schemas_have_a_script() -> None:
    import pytest

    with pytest.raises(TypeError):
        structured(ScriptedModelFactory(), dict)


def test_every_built_in_script_has_no_digits_and_no_number_words() -> None:
    for text in all_wording():
        assert not re.search(r"\d", text), text
        assert not NUMBER_WORDS.search(text), text


def test_every_built_in_script_passes_the_shape_checks() -> None:
    for scenario in (*SCENARIOS, ADD_PENDING):
        if scenario.summary:
            assert shape_ok(scenario.summary), scenario.key
        if scenario.verdict:
            assert verdict_ok(scenario.verdict), scenario.key
    assert shape_ok(DEFAULT_REPORT) and verdict_ok(DEFAULT_VERDICT)


async def test_the_engine_accepts_the_scripted_report_as_llm_output() -> None:
    factory = ScriptedModelFactory()
    engine = ImpactEngine(SETTINGS, POLICY, model_factory=factory)
    result = await engine.summarize(
        action_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        intent=REPLIT_INTENT,
        classification=classified(REPLIT_SQL),
        diff=replit_diff(),
        retrieval=None,
    )
    assert result.source == "llm" and result.fallback_reason is None
    assert result.facts.total_rows_affected == 245_677  # measured, never from the wording
    assert result.cost_usd is not None and result.cost_usd > Decimal(0)


def test_every_scenario_ends_in_a_status_its_steps_can_reach() -> None:
    keys = [s.key for s in SCENARIOS]
    assert len(keys) == len(set(keys))
    for scenario in SCENARIOS:
        if scenario.then == "approve":
            assert scenario.expect.value == "committed"
        if scenario.then == "reject":
            assert scenario.expect.value == "rejected" and scenario.reject_note
        if scenario.then == "expire":
            assert scenario.expect.value == "expired"
