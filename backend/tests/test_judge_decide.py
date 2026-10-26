"""Step 13 (BE-73, BE-74, ARCH-09): `decide` makes a decision stricter, never looser, and a judge
that failed or never ran is at least review with `ai_unavailable`."""

import itertools
from decimal import Decimal

import pytest

from app.db.enums import Decision
from app.services.judge import (
    INJECTION_REASON,
    JUDGE_UNAVAILABLE_REASON,
    LOW_CONFIDENCE_REASON,
    InjectionHit,
    JudgeFailure,
    JudgeResult,
    decide,
)
from tests.judge_helpers import result_of, rule_decision

A, R, B = Decision.ALLOW, Decision.REVIEW, Decision.BLOCK
MIN = 0.6
RANK = {A: 0, R: 1, B: 2}
HIT = InjectionHit("intent", "ignore_instructions")
FAILURES: tuple[JudgeFailure, ...] = (
    "not_configured",
    "llm_timeout",
    "llm_error",
    "parse_error",
    "bad_shape",
    "judge_error",
    "skipped_cost_cap",
)


def failed(failure: JudgeFailure) -> JudgeResult:
    return JudgeResult.unavailable("llm", failure)


@pytest.mark.parametrize(
    ("rules", "recommendation", "confidence", "final", "deciding"),
    [
        (R, "allow", 0.99, R, "rules"),  # the judge cannot lower review
        (R, "review", 0.9, R, "rules"),
        (R, "block", 0.5, B, "judge"),  # a low-confidence block still blocks
        (A, "block", 0.99, B, "judge"),  # escalates allow to block
        (A, "review", 0.2, R, "judge"),
        (A, "allow", 0.99, A, "rules"),
        (A, "allow", 0.6, A, "rules"),  # exactly the minimum allows
        (A, "allow", 0.59, R, "low_confidence"),  # just under it does not
        (A, "allow", 0.0, R, "low_confidence"),
        (R, "allow", 0.1, R, "rules"),
    ],
)
def test_the_strictest_outcome_wins(
    rules: Decision, recommendation: str, confidence: float, final: Decision, deciding: str
) -> None:
    out = decide(
        rule_decision(rules),
        result_of(recommendation, confidence=confidence),
        [],
        min_confidence=MIN,
    )
    assert out.decision == final
    assert out.deciding == deciding
    assert out.ai_unavailable is False


def test_a_block_from_the_rules_is_final_and_ignores_the_judge() -> None:
    rules = rule_decision(B, "Deleting without WHERE is blocked.")
    out = decide(rules, result_of("allow"), [HIT], min_confidence=MIN)
    assert out.decision == B and out.deciding == "rules"
    assert out.reason == "Deleting without WHERE is blocked."
    assert out.risk_level is None and out.ai_unavailable is False
    assert out.injection == (HIT,)  # still reported
    # A judge that failed does not matter either: nothing could be loosened, and nothing was asked.
    assert decide(rules, None, [], min_confidence=MIN).ai_unavailable is False


def test_the_reason_and_risk_level_come_from_the_deciding_input() -> None:
    judged = result_of("block", risk_level="critical")
    out = decide(rule_decision(A, "A small insert."), judged, [], min_confidence=MIN)
    assert out.reason == "Because." and out.risk_level == "critical"
    out = decide(
        rule_decision(R, "Large change."),
        result_of("allow", risk_level="low"),
        [],
        min_confidence=MIN,
    )
    assert out.reason == "Large change." and out.risk_level == "low"  # the judge's level, kept


def test_a_low_confidence_allow_explains_itself() -> None:
    out = decide(rule_decision(A), result_of("allow", confidence=0.3), [], min_confidence=MIN)
    assert out.decision == R and out.reason == LOW_CONFIDENCE_REASON


@pytest.mark.parametrize("rules", [A, R])
@pytest.mark.parametrize("failure", FAILURES)
def test_a_failed_judge_is_at_least_review_with_ai_unavailable(
    rules: Decision, failure: JudgeFailure
) -> None:
    out = decide(rule_decision(rules), failed(failure), [], min_confidence=MIN)
    assert out.decision == R
    assert out.ai_unavailable is True and out.risk_level is None
    assert out.deciding == ("judge_unavailable" if rules == A else "rules")
    if rules == A:
        assert out.reason == JUDGE_UNAVAILABLE_REASON


@pytest.mark.parametrize("rules", [A, R])
def test_no_judge_at_all_is_review_with_ai_unavailable(rules: Decision) -> None:
    out = decide(rule_decision(rules), None, [], min_confidence=MIN)
    assert out.decision == R and out.ai_unavailable is True


def test_a_verdict_next_to_a_failure_is_not_trusted() -> None:
    odd = JudgeResult(
        "llm", None, result_of("allow").verdict, "llm_error", False, None, None, None, 1
    )
    out = decide(rule_decision(A), odd, [], min_confidence=MIN)
    assert out.decision == R and out.ai_unavailable is True


@pytest.mark.parametrize(
    ("rules", "recommendation", "final", "deciding"),
    [
        (A, "allow", R, "injection"),  # the finding: injection with allow and allow is review
        (R, "allow", R, "rules"),
        (A, "review", R, "injection"),
        (A, "block", B, "judge"),  # the judge can still make it stricter
        (R, "block", B, "judge"),
    ],
)
def test_injection_forces_at_least_review(
    rules: Decision, recommendation: str, final: Decision, deciding: str
) -> None:
    out = decide(rule_decision(rules), result_of(recommendation), [HIT], min_confidence=MIN)
    assert out.decision == final and out.deciding == deciding
    assert out.injection == (HIT,)
    if deciding == "injection":
        assert out.reason == INJECTION_REASON


def test_injection_and_a_failed_judge_are_both_reported() -> None:
    out = decide(rule_decision(A), failed("llm_error"), [HIT], min_confidence=MIN)
    assert out.decision == R and out.ai_unavailable is True and out.deciding == "injection"


def test_the_confidence_rule_follows_the_minimum_it_is_given() -> None:
    judged = result_of("allow", confidence=0.7)
    assert decide(rule_decision(A), judged, [], min_confidence=0.7).decision == A
    assert decide(rule_decision(A), judged, [], min_confidence=0.71).decision == R


def test_every_combination_is_at_least_as_strict_as_the_rules() -> None:
    judges: list[JudgeResult | None] = [None, *(failed(f) for f in FAILURES)]
    judges += [
        result_of(rec, confidence=c)
        for rec, c in itertools.product(("allow", "review", "block"), (0.0, 0.59, 0.6, 1.0))
    ]
    seen = set()
    for rules, judge, hits in itertools.product((A, R, B), judges, ([], [HIT])):
        out = decide(rule_decision(rules), judge, hits, min_confidence=MIN)
        assert RANK[out.decision] >= RANK[rules], (rules, judge, hits)
        if judge is None or judge.failure is not None:
            assert out.decision != A
        if hits:
            assert out.decision != A
        if rules != B and (judge is None or judge.failure is not None):
            assert out.ai_unavailable
        seen.add(out.decision)
    assert seen == {A, R, B}


def test_the_unavailable_helper_records_no_call() -> None:
    result = JudgeResult.unavailable("llm", "skipped_cost_cap", model="test:model")
    assert result.verdict is None and result.failure == "skipped_cost_cap"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (0, 0, Decimal(0))
    assert result.to_json()["failure"] == "skipped_cost_cap"
    assert result.to_json()["result"] is None and result.to_json()["cost_usd"] == "0"
