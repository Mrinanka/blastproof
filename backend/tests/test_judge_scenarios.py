"""Step 13 Done-when (DW-B13) on the demo scenarios, with a fake judge model and no database.

The diffs are the measured impact of each scenario (`test_seed_counts` proves those numbers), so
the rule decisions here are the ones the shadow run's facts give. EVAL-05 (step 13 part): whatever
the judge says, the decision is never looser than the rules'.
"""

from datetime import UTC, datetime

import pytest

from app.db.enums import Decision
from app.services.classifier import classify
from app.services.judge import assess_safely, decide, injection_signals
from app.services.policy import PolicyDecision, Route, evaluate_pass_one
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import SCENARIOS_DIR
from tests.impact_helpers import POLICY
from tests.judge_helpers import (
    FakeJudgeLlm,
    context_for,
    judge_with,
    measured_scenarios,
    replit_context,
    verdict,
)

MIN = 0.6
RANK = {Decision.ALLOW: 0, Decision.REVIEW: 1, Decision.BLOCK: 2}
MEASURED = measured_scenarios()
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def scenario(name: str) -> SqlScenario:
    return next(s for s in MEASURED if s.id == name)


async def judged(context: object, recommendation: str, confidence: float = 0.99) -> object:
    llm = FakeJudgeLlm(parsed=verdict(recommendation, "high", confidence, "Because."))
    judge, _ = judge_with(llm)
    return await assess_safely(judge, context, 5), llm  # type: ignore[arg-type]


# --- DW-B13 (1): escalates a risky "allowed" action ------------------------------------------


async def test_a_judge_block_escalates_an_allowed_small_insert() -> None:
    context = context_for(scenario("note-on-customer-42"))
    assert context.rules.decision == Decision.ALLOW  # the rules let it through
    result, _ = await judged(context, "block")
    final = decide(context.rules, result, injection_signals(context), min_confidence=MIN)
    assert final.decision == Decision.BLOCK and final.deciding == "judge"


async def test_a_judge_review_escalates_an_allowed_write_to_review() -> None:
    context = context_for(scenario("open-support-ticket"))
    assert context.rules.decision == Decision.ALLOW
    result, _ = await judged(context, "review", 0.9)
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.REVIEW


async def test_a_judge_block_escalates_a_review_to_block() -> None:
    context = context_for(scenario("delete-inactive-enterprise-customer"))
    assert context.rules.decision == Decision.REVIEW
    result, _ = await judged(context, "block")
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.BLOCK


# --- DW-B13 (2): cannot downgrade -------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["drop-users-table", "delete-users-without-where", "delete-orders-where-true"]
)
async def test_a_write_the_rules_block_stays_blocked_whatever_the_judge_says(
    name: str,
) -> None:
    all_scenarios = [
        s
        for f in ("incidents.yaml", "labeled.yaml")
        for s in load_scenario_file(SCENARIOS_DIR / f).scenarios
        if isinstance(s, SqlScenario)
    ]
    blocked = next(s for s in all_scenarios if s.id == name)
    one = evaluate_pass_one(POLICY, classify(blocked.sql, now=NOW))
    assert one.route == Route.BLOCK
    rules = PolicyDecision(Decision.BLOCK, one.matched, one.reason)

    # No judge result at all is fine for a block: nothing was asked, nothing is "unavailable".
    final = decide(rules, None, (), min_confidence=MIN)
    assert final.decision == Decision.BLOCK and final.ai_unavailable is False
    # And even if a judge had said allow, the answer is the same.
    result, _ = await judged(replit_context(), "allow")
    assert decide(rules, result, (), min_confidence=MIN).decision == Decision.BLOCK  # type: ignore[arg-type]


async def test_the_replit_review_stays_review_when_the_judge_says_allow() -> None:
    context = replit_context()
    assert context.rules.decision == Decision.REVIEW
    result, _ = await judged(context, "allow", 0.99)
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.REVIEW and final.deciding == "rules"


# --- DW-B13 (3): a failing judge is review, never allow ----------------------------------------


@pytest.mark.parametrize(
    "llm",
    [
        FakeJudgeLlm(raises=RuntimeError("boom")),
        FakeJudgeLlm(hang=True),
        FakeJudgeLlm(parsed=None, parsing_error=ValueError("bad json")),
        FakeJudgeLlm(output="garbage"),
    ],
    ids=["raises", "hangs", "unparseable", "not-a-mapping"],
)
async def test_a_failing_judge_on_an_allowed_write_is_review_with_ai_unavailable(
    llm: FakeJudgeLlm,
) -> None:
    context = context_for(scenario("note-on-customer-42"))
    judge, _ = judge_with(llm, timeout_seconds=0.05)
    result = await assess_safely(judge, context, 1)
    assert result.failure is not None and result.verdict is None
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.REVIEW and final.ai_unavailable is True


# --- EVAL-05 sweep ----------------------------------------------------------------------------


@pytest.mark.parametrize("item", MEASURED, ids=lambda s: s.id)
async def test_an_always_allow_judge_never_changes_the_rule_decision(item: SqlScenario) -> None:
    context = context_for(item)
    result, _ = await judged(context, "allow", 0.99)
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == context.rules.decision


@pytest.mark.parametrize("item", MEASURED, ids=lambda s: s.id)
async def test_an_always_block_judge_blocks_every_write(item: SqlScenario) -> None:
    context = context_for(item)
    result, _ = await judged(context, "block")
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert final.decision == Decision.BLOCK


@pytest.mark.parametrize("item", MEASURED, ids=lambda s: s.id)
async def test_a_failed_judge_is_never_looser_than_review_on_any_scenario(
    item: SqlScenario,
) -> None:
    context = context_for(item)
    judge, _ = judge_with(FakeJudgeLlm(raises=RuntimeError("boom")))
    result = await assess_safely(judge, context, 1)
    final = decide(context.rules, result, (), min_confidence=MIN)
    assert RANK[final.decision] >= max(RANK[context.rules.decision], RANK[Decision.REVIEW])
    # A rule block is final: the judge is not consulted, so nothing is unavailable.
    assert final.ai_unavailable is (context.rules.decision != Decision.BLOCK)


def test_the_sweep_covers_every_decision() -> None:
    decisions = {context_for(s).rules.decision for s in MEASURED}
    assert decisions == {Decision.ALLOW, Decision.REVIEW, Decision.BLOCK}
