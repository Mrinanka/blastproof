"""Step 13 (BE-74, ARCH-09): any `Judge` implementation that raises, hangs or returns garbage is a
failure, and a failure is review through `decide`, never an allow."""

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from app.db.enums import Decision
from app.services.judge import JudgeContext, JudgeResult, assess_safely, decide
from tests.judge_helpers import replit_context, result_of, rule_decision, verdict


class Raising:
    provider = "raising"

    async def assess(self, context: JudgeContext) -> JudgeResult:
        raise RuntimeError("SECRET-EXCEPTION-TEXT")


class Hanging:
    provider = "hanging"

    async def assess(self, context: JudgeContext) -> JudgeResult:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class Returning:
    provider = "returning"

    def __init__(self, value: Any) -> None:
        self.value = value

    async def assess(self, context: JudgeContext) -> JudgeResult:
        return self.value


def odd(**changes: Any) -> JudgeResult:
    base = result_of("allow")
    return JudgeResult(**{**base.__dict__, **changes})


async def test_a_raising_judge_is_judge_error() -> None:
    result = await assess_safely(Raising(), replit_context(), 1)
    assert result.failure == "judge_error" and result.verdict is None
    assert result.provider == "raising" and result.cost_usd is None


async def test_a_hanging_judge_is_a_timeout_in_under_two_seconds() -> None:
    result = await assess_safely(Hanging(), replit_context(), 0.05)
    assert result.failure == "llm_timeout" and result.duration_ms < 2_000


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        "allow",
        {"verdict": "allow"},
        odd(verdict=None, failure=None),  # no verdict and no failure
        odd(failure="llm_error"),  # a verdict and a failure
        odd(verdict=verdict(confidence=7.0)),  # out of bounds
        odd(verdict=verdict(reason="  ")),
    ],
    ids=["none", "string", "dict", "neither", "both", "confidence", "blank-reason"],
)
async def test_a_malformed_result_is_judge_error(garbage: Any) -> None:
    result = await assess_safely(Returning(garbage), replit_context(), 1)
    assert result.failure == "judge_error" and result.verdict is None


async def test_a_good_result_is_returned_as_it_is() -> None:
    good = result_of("review")
    assert await assess_safely(Returning(good), replit_context(), 1) is good


async def test_a_failure_the_judge_reports_is_returned_as_it_is() -> None:
    failed = JudgeResult.unavailable("returning", "skipped_cost_cap")
    assert await assess_safely(Returning(failed), replit_context(), 1) is failed


@pytest.mark.parametrize("judge", [Raising(), Hanging()], ids=["raises", "hangs"])
async def test_a_failing_judge_on_an_allowed_write_is_review_not_allow(judge: Any) -> None:
    result = await assess_safely(judge, replit_context(), 0.05)
    final = decide(rule_decision(Decision.ALLOW), result, [], min_confidence=0.6)
    assert final.decision == Decision.REVIEW and final.ai_unavailable is True


async def test_a_cancelled_call_is_not_swallowed() -> None:
    task = asyncio.create_task(assess_safely(Hanging(), replit_context(), 30))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_logs_hold_no_exception_text(capsys: pytest.CaptureFixture[str]) -> None:
    from app.logging import configure_logging

    configure_logging()
    await assess_safely(Raising(), replit_context(), 1)
    logged = capsys.readouterr().out
    assert "judge_failed" in logged and "RuntimeError" in logged
    assert "SECRET-EXCEPTION-TEXT" not in logged


def test_unavailable_results_cost_nothing() -> None:
    result = JudgeResult.unavailable("llm", "not_configured")
    assert result.cost_usd == Decimal(0)
