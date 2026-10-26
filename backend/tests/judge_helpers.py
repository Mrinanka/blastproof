"""Test doubles for step 13: a structured judge model that needs no network, judge contexts from
the scenarios, and verdicts.

`FakeJudgeLlm` is step 12's `FakeLlm` with a verdict as its parsed output; `FakeFactory` and the
other doubles are reused as they are.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.config import Settings
from app.db.enums import Decision
from app.services.classifier import Classification
from app.services.impact import build_facts, template_report
from app.services.judge import (
    JudgeContext,
    JudgeResult,
    JudgeVerdict,
    LLMJudge,
)
from app.services.knowledge import Retrieval, RetrievedChunk
from app.services.policy import PolicyDecision, RuleMatch, evaluate_pass_two
from app.services.shadow import CascadePath, Diff, SampleRow
from evals.schema import SqlScenario
from tests.impact_helpers import (
    POLICY,
    REPLIT_CASCADES,
    REPLIT_INTENT,
    REPLIT_SQL,
    FakeFactory,
    FakeLlm,
    classified,
    diff_from,
    replit_diff,
    scenarios_with_impact,
)

ACTION_ID = UUID("00000000-0000-4000-8000-000000000013")
AGENT_ID = UUID("00000000-0000-4000-8000-0000000000b3")

GOOD_REASON = "Deletes 48,213 users including 12 paying customers; a person should look first."


@dataclass
class FakeJudgeLlm(FakeLlm):
    """The structured runnable for `JudgeVerdict`; every behaviour of `FakeLlm` still applies."""

    parsed: JudgeVerdict | dict[str, Any] | None = None  # type: ignore[assignment]


def verdict(
    recommendation: str = "review",
    risk_level: str = "high",
    confidence: float = 0.9,
    reason: str = GOOD_REASON,
) -> JudgeVerdict:
    return JudgeVerdict(
        recommendation=recommendation,  # type: ignore[arg-type]
        risk_level=risk_level,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
    )


def judge_with(
    llm: FakeLlm, *, failures: int = 0, timeout_seconds: float | None = None, **settings: Any
) -> tuple[LLMJudge, FakeFactory]:
    factory = FakeFactory(llm, failures=failures)
    values: dict[str, Any] = {"app_env": "test"} | settings
    judge = LLMJudge(
        Settings(_env_file=None, **values),
        POLICY,
        model_factory=factory,
        timeout_seconds=timeout_seconds,
    )
    return judge, factory


def rules_for(classification: Classification, diff: Diff) -> PolicyDecision:
    return evaluate_pass_two(POLICY, classification, diff.facts())


def context_for_diff(
    sql: str,
    diff: Diff,
    *,
    intent: str = REPLIT_INTENT,
    retrieval: Retrieval | None = None,
    rules: PolicyDecision | None = None,
) -> JudgeContext:
    classification = classified(sql)
    facts = build_facts(diff, classification, POLICY)
    return JudgeContext(
        action_id=ACTION_ID,
        agent_id=AGENT_ID,
        intent=intent,
        classification=classification,
        diff=diff,
        facts=facts,
        report=template_report(facts),
        rules=rules if rules is not None else rules_for(classification, diff),
        retrieval=retrieval,
    )


def replit_context(
    *,
    samples: Sequence[SampleRow] = (),
    retrieval: Retrieval | None = None,
    intent: str | None = None,
) -> JudgeContext:
    return context_for_diff(
        REPLIT_SQL,
        replit_diff(cascades=REPLIT_CASCADES, samples=samples),
        retrieval=retrieval,
        intent=intent if intent is not None else REPLIT_INTENT,
    )


def context_for(scenario: SqlScenario) -> JudgeContext:
    """A context from a scenario's measured impact (the diff the shadow run would produce)."""
    assert scenario.expected_impact is not None
    classification = classified(scenario.sql)
    diff = diff_from(
        scenario.expected_impact, classification.target_tables, cascades=_cascades(scenario)
    )
    return context_for_diff(scenario.sql, diff, intent=scenario.intent)


def _cascades(scenario: SqlScenario) -> tuple[CascadePath, ...]:
    return REPLIT_CASCADES if scenario.id == "replit-clean-up-inactive-users" else ()


def measured_scenarios() -> list[SqlScenario]:
    return scenarios_with_impact()


def chunk(content: str, *, source: str = "notes.md", tables: Sequence[str] = ()) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        source=source,
        table_names=list(tables),
        content=content,
        match="table" if tables else "similar",
        score=None if tables else 0.8,
    )


def retrieval_of(*chunks: RetrievedChunk) -> Retrieval:
    return Retrieval(chunks=list(chunks), refs=[], duration_ms=1)


def result_of(
    recommendation: str, *, confidence: float = 0.99, risk_level: str = "low"
) -> JudgeResult:
    """A successful judge result for `decide`, without a model."""
    return JudgeResult(
        "llm",
        "test:model",
        verdict(recommendation, risk_level, confidence, "Because."),
        None,
        False,
        10,
        5,
        None,
        1,
    )


def rule_decision(outcome: Decision, reason: str = "Because the rules say so.") -> PolicyDecision:
    return PolicyDecision(outcome, (RuleMatch("Some rule", outcome, reason),), reason)
