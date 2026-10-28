"""`--mode rules`: replay the scenario files through the policy rules and the hook rules only.

No Docker, no model, deterministic. SQL goes through the classifier and the same two policy passes
the graph runs; the facts for pass 2 are the scenario's `expected_impact` (proved against the seed
by `tests/test_seed_counts.py`) on the seed's row counts, so no shadow run is needed. Hook calls go
through `hook_rules.evaluate`.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from app.services import hook_rules
from app.services.classifier import classify
from app.services.policy import Policy, Route, evaluate_pass_one, evaluate_pass_two
from evals.facts import facts_from
from evals.metrics import Outcome, PathKind
from evals.schema import HookScenario, SqlScenario, load_scenario_file

# The demo workspace path the hook tests use (D-238): the hook rules read paths, not the disk.
HOOK_CWD = r"D:\My Workplace\blastproof-demo"


class EvalError(Exception):
    """A scenario that cannot be judged; its message is safe to print (it holds no SQL)."""


def error_text(exc: Exception) -> str:
    return str(exc) if isinstance(exc, EvalError) else type(exc).__name__


def anchor_time(anchor: date) -> datetime:
    """Midnight UTC on the anchor date: the clock the classifier pins `now()` to."""
    return datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)


def sql_outcome(scenario: SqlScenario, file: str, policy: Policy, now: datetime) -> Outcome:
    def outcome(actual: str, path: PathKind, reason: str, rules: Sequence[str]) -> Outcome:
        return Outcome(
            id=scenario.id,
            file=file,
            kind="sql",
            expected=scenario.expected_decision,
            actual=actual,
            incident=scenario.incident is not None,
            path=path,
            reason=reason,
            rules=tuple(rules),
        )

    c = classify(scenario.sql, now=now)
    one = evaluate_pass_one(policy, c)
    if one.route == Route.BLOCK:
        return outcome("block", "blocked", one.reason, [m.name for m in one.matched])
    if one.route == Route.FAST_LANE:
        return outcome("allow", "fast_lane", one.reason, [m.name for m in one.matched])
    if scenario.expected_impact is None:
        raise EvalError("needs a preview but the scenario has no expected_impact")
    facts = facts_from(scenario.expected_impact, c.target_tables)
    two = evaluate_pass_two(policy, c, facts)
    return outcome(two.decision.value, "previewed", two.reason, [m.name for m in two.matched])


def hook_outcome(scenario: HookScenario, file: str) -> Outcome:
    verdict = hook_rules.evaluate(scenario.tool_name, scenario.tool_input, HOOK_CWD, HOOK_CWD)
    return Outcome(
        id=scenario.id,
        file=file,
        kind="hook",
        expected="block" if scenario.expected_decision == "deny" else "allow",
        actual="block" if verdict.decision == "deny" else "allow",
        incident=True,
        path="hook",
        reason=verdict.reason,
        rules=(verdict.rule,) if verdict.rule else (),
    )


def run_rules(scenario_files: Sequence[Path], policy: Policy) -> list[Outcome]:
    """One outcome per scenario, in file order. A scenario that raises becomes an error outcome."""
    outcomes: list[Outcome] = []
    for path in scenario_files:
        loaded = load_scenario_file(path)
        now = anchor_time(loaded.anchor_date)
        for scenario in loaded.scenarios:
            try:
                if isinstance(scenario, SqlScenario):
                    outcomes.append(sql_outcome(scenario, path.name, policy, now))
                else:
                    outcomes.append(hook_outcome(scenario, path.name))
            except Exception as exc:
                outcomes.append(
                    Outcome(
                        id=scenario.id,
                        file=path.name,
                        kind=scenario.kind,
                        expected=_expected(scenario),
                        actual=None,
                        incident=getattr(scenario, "incident", None) is not None,
                        error=error_text(exc),
                    )
                )
    return outcomes


def _expected(scenario: SqlScenario | HookScenario) -> str:
    if isinstance(scenario, HookScenario):
        return "block" if scenario.expected_decision == "deny" else "allow"
    return scenario.expected_decision
