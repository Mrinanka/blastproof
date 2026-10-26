"""Step 13 (BE-71, BE-74, BE-75, SEC-12): `LLMJudge` calls the model once, checks what comes back,
and returns a failure instead of raising."""

import asyncio
import json
import math
from decimal import Decimal

import pytest

from app.logging import configure_logging
from app.services.judge import JudgeResult, JudgeVerdict
from app.services.llm import LlmConfigError
from tests.judge_helpers import (
    ACTION_ID,
    AGENT_ID,
    FakeJudgeLlm,
    judge_with,
    replit_context,
    verdict,
)

GOOD = verdict("review", "high", 0.85)
# 1,200 x $0.75 + 300 x $3.75, per million tokens
GOOD_COST = Decimal("0.002025")


def assert_failure(result: JudgeResult, failure: str) -> None:
    assert result.verdict is None and result.failure == failure
    assert result.reason_withheld is False


async def test_a_good_verdict_comes_back_with_its_tokens_and_cost() -> None:
    llm = FakeJudgeLlm(parsed=GOOD)
    judge, factory = judge_with(llm)
    result = await judge.assess(replit_context())
    assert result.failure is None and result.verdict == GOOD
    assert result.provider == "llm" and result.model == "google_genai:gemini-3.8-flash"
    assert (result.input_tokens, result.output_tokens) == (1_200, 300)
    assert result.cost_usd == GOOD_COST and result.reason_withheld is False
    assert factory.models[0].structured_calls == [(JudgeVerdict, True)]
    assert result.to_json() == {
        "provider": "llm",
        "model": "google_genai:gemini-3.8-flash",
        "result": GOOD.model_dump(),
        "failure": None,
        "reason_withheld": False,
        "input_tokens": 1_200,
        "output_tokens": 300,
        "cost_usd": "0.002025",
        "duration_ms": result.duration_ms,
    }


async def test_a_dict_from_the_model_is_accepted() -> None:
    judge, _ = judge_with(FakeJudgeLlm(parsed=GOOD.model_dump()))
    assert (await judge.assess(replit_context())).verdict == GOOD


async def test_the_run_config_names_the_action_agent_and_env() -> None:
    llm = FakeJudgeLlm(parsed=GOOD)
    judge, _ = judge_with(llm)
    await judge.assess(replit_context())
    [(messages, config)] = llm.calls
    assert config == {
        "run_name": "risk_judge",
        "metadata": {"action_id": str(ACTION_ID), "agent_id": str(AGENT_ID), "env": "test"},
        "tags": ["judge"],
    }
    assert [m.type for m in messages] == ["system", "human"]


async def test_the_model_options_reach_the_factory() -> None:
    judge, factory = judge_with(
        FakeJudgeLlm(parsed=GOOD), judge_model_options={"thinking_level": "low"}
    )
    await judge.assess(replit_context())
    assert factory.options_seen == [{"thinking_level": "low"}]


async def test_a_raising_model_is_llm_error_with_unknown_cost() -> None:
    judge, _ = judge_with(FakeJudgeLlm(raises=RuntimeError("quota for key AIza-secret")))
    result = await judge.assess(replit_context())
    assert_failure(result, "llm_error")
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (None, None, None)


async def test_a_hanging_model_times_out_quickly() -> None:
    judge, _ = judge_with(FakeJudgeLlm(hang=True), timeout_seconds=0.05)
    result = await judge.assess(replit_context())
    assert_failure(result, "llm_timeout")
    assert result.cost_usd is None and result.duration_ms < 2_000


def test_the_default_timeout_is_llm_timeout_seconds() -> None:
    judge, _ = judge_with(FakeJudgeLlm(), llm_timeout_seconds=7)
    assert judge._timeout == 7


@pytest.mark.parametrize(
    "llm",
    [
        FakeJudgeLlm(parsed=None, parsing_error=ValueError("bad json")),
        FakeJudgeLlm(parsed=GOOD, parsing_error=ValueError("partly bad")),
        FakeJudgeLlm(parsed=None),
        FakeJudgeLlm(parsed={"recommendation": "allow"}),
        FakeJudgeLlm(
            parsed={"recommendation": "yes", "risk_level": "low", "confidence": 1, "reason": "x"}
        ),
        FakeJudgeLlm(
            parsed={"recommendation": "allow", "risk_level": "tiny", "confidence": 1, "reason": "x"}
        ),
    ],
    ids=["error", "error-with-parsed", "parsed-none", "missing-fields", "bad-enum", "bad-risk"],
)
async def test_an_unparseable_result_is_parse_error_and_keeps_tokens(llm: FakeJudgeLlm) -> None:
    judge, _ = judge_with(llm)
    result = await judge.assess(replit_context())
    assert_failure(result, "parse_error")
    assert (result.input_tokens, result.output_tokens) == (1_200, 300)
    assert result.cost_usd == GOOD_COST


async def test_a_result_that_is_not_a_mapping_has_unknown_cost() -> None:
    judge, _ = judge_with(FakeJudgeLlm(output=["not", "a", "dict"]))
    result = await judge.assess(replit_context())
    assert_failure(result, "parse_error")
    assert result.cost_usd is None  # no raw message, so the usage is unknown


@pytest.mark.parametrize(
    "bad",
    [
        verdict(confidence=-0.1),
        verdict(confidence=1.5),
        verdict(confidence=math.nan),
        verdict(confidence=math.inf),
        verdict(reason=""),
        verdict(reason="   \n"),
        verdict(reason="x" * 501),
    ],
    ids=["negative", "above-one", "nan", "inf", "empty", "blank", "long"],
)
async def test_a_verdict_out_of_bounds_is_bad_shape(bad: JudgeVerdict) -> None:
    judge, _ = judge_with(FakeJudgeLlm(parsed=bad))
    result = await judge.assess(replit_context())
    assert_failure(result, "bad_shape")
    assert result.cost_usd == GOOD_COST


@pytest.mark.parametrize("confidence", [0.0, 1.0])
async def test_the_limits_themselves_are_accepted(confidence: float) -> None:
    edge = verdict(confidence=confidence, reason="x" * 500)
    judge, _ = judge_with(FakeJudgeLlm(parsed=edge))
    assert (await judge.assess(replit_context())).verdict == edge


async def test_a_model_that_cannot_be_built_is_not_configured_then_retried() -> None:
    llm = FakeJudgeLlm(parsed=GOOD)
    judge, factory = judge_with(llm, failures=1)
    first = await judge.assess(replit_context())
    assert_failure(first, "not_configured")
    assert (first.input_tokens, first.output_tokens, first.cost_usd) == (0, 0, Decimal(0))
    assert llm.calls == []
    second = await judge.assess(replit_context())
    assert second.verdict == GOOD
    await judge.assess(replit_context())
    assert factory.builds == 2  # retried once after the failure, then cached


def test_a_model_without_a_price_stops_the_judge() -> None:
    with pytest.raises(LlmConfigError, match="LLM_PRICES has no entry"):
        judge_with(FakeJudgeLlm(), judge_model="anthropic:claude-haiku-5-5")


def test_nothing_is_built_until_the_first_assessment() -> None:
    judge, factory = judge_with(FakeJudgeLlm())
    assert factory.builds == 0 and judge._model._value is None


async def test_missing_usage_leaves_tokens_and_cost_unknown() -> None:
    judge, _ = judge_with(FakeJudgeLlm(parsed=GOOD, usage=None))
    result = await judge.assess(replit_context())
    assert result.verdict == GOOD
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (None, None, None)


async def test_personal_data_in_the_reason_is_masked() -> None:
    judge, _ = judge_with(
        FakeJudgeLlm(parsed=verdict(reason="Deletes 48,213 users such as bob@example.com."))
    )
    result = await judge.assess(replit_context())
    assert result.verdict is not None and result.reason_withheld is False
    assert result.verdict.reason == "Deletes 48,213 users such as [email]."


async def test_an_invented_number_withholds_the_reason_but_keeps_the_verdict() -> None:
    judge, _ = judge_with(
        FakeJudgeLlm(parsed=verdict("block", "critical", 0.95, "Deletes 13 paying customers."))
    )
    result = await judge.assess(replit_context())
    assert result.failure is None and result.reason_withheld is True
    assert result.verdict is not None
    assert (result.verdict.recommendation, result.verdict.risk_level) == ("block", "critical")
    assert result.verdict.confidence == 0.95
    assert result.verdict.reason == (
        "The AI risk check recommended block (critical risk), but its written explanation was"
        " withheld because it cited numbers that were not measured."
    )
    assert result.cost_usd == GOOD_COST
    assert result.to_json()["reason_withheld"] is True


@pytest.mark.parametrize(
    "reason",
    [
        "Deletes 48,213 users and 12 paying customers.",
        "Touches 96.4% of the table.",  # a measured percentage
        "It changes 245,677 rows across 7 tables.",
        "Over the 1,000 row limit in the rules.",  # a rule threshold
        "No numbers here at all.",
    ],
)
async def test_measured_numbers_are_allowed(reason: str) -> None:
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict(reason=reason)))
    result = await judge.assess(replit_context())
    assert result.reason_withheld is False, reason


async def test_a_number_written_as_a_word_is_checked_too() -> None:
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict(reason="Deletes about ninety customers.")))
    assert (await judge.assess(replit_context())).reason_withheld is True


async def test_a_number_only_the_rules_reason_holds_is_allowed() -> None:
    context = replit_context()
    rules = context.rules.__class__(
        context.rules.decision, context.rules.matched, "Needs 30 days of notice."
    )
    context = context.__class__(**{**context.__dict__, "rules": rules})
    judge, _ = judge_with(FakeJudgeLlm(parsed=verdict(reason="The rules ask for 30 days.")))
    assert (await judge.assess(context)).reason_withheld is False


async def test_concurrent_assessments_share_one_built_model() -> None:
    llm = FakeJudgeLlm(parsed=GOOD)
    judge, factory = judge_with(llm)
    results = await asyncio.gather(*(judge.assess(replit_context()) for _ in range(5)))
    assert {r.failure for r in results} == {None}
    assert factory.builds == 1 and len(llm.calls) == 5


SECRET_REASON = "SECRET-REASON-TEXT"


@pytest.mark.parametrize(
    "llm",
    [
        FakeJudgeLlm(parsed=verdict(reason=f"Deletes 48,213 users: {SECRET_REASON}.")),
        FakeJudgeLlm(parsed=verdict(reason=f"Deletes 99,999 users: {SECRET_REASON}.")),
        FakeJudgeLlm(raises=RuntimeError("SECRET-EXCEPTION-TEXT leak.me@example.com")),
    ],
    ids=["success", "number-check", "error"],
)
async def test_logs_hold_no_reason_sql_prompt_or_exception_text(
    llm: FakeJudgeLlm, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    judge, _ = judge_with(llm)
    await judge.assess(replit_context())
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "judge_done" in logged  # the log is being captured at all
    for line in logged.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            if record.get("event") == "judge_done":
                assert record["action_id"] == str(ACTION_ID)
                assert {"recommendation", "failure", "duration_ms", "cost_usd"} <= set(record)
    for secret in (
        SECRET_REASON,
        "SECRET-EXCEPTION-TEXT",
        "leak.me",
        "DELETE FROM",
        "Clean up inactive users",
        "untrusted_data",
    ):
        assert secret not in logged


async def test_a_build_failure_logs_only_the_class(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    judge, _ = judge_with(FakeJudgeLlm(parsed=GOOD), failures=1)
    await judge.assess(replit_context())
    logged = capsys.readouterr().out
    assert "llm_build_failed" in logged and "RuntimeError" in logged
    assert "secret-option-value" not in logged
