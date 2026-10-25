"""Step 8 "Done when": the classifier-relevant incident scenarios pass.

The facts come from the classifier and the rules from `policies/rules.yaml`, evaluated through
pass 1 as the workflow will (step 9 replaced this file's local mirror of the SQL-only rules).
"""

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.services.classifier import Kind, classify
from app.services.policy import Route, evaluate_pass_one, load_policy
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import SCENARIOS_DIR

POLICY = load_policy(DEFAULT_POLICY_FILE)
FAST_LANE_NOTE = "Fast-lane note"


def sql_scenarios() -> list[SqlScenario]:
    found: list[SqlScenario] = []
    for name in ("incidents.yaml", "labeled.yaml"):
        for scenario in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if isinstance(scenario, SqlScenario):
                found.append(scenario)
    return found


SCENARIOS = sql_scenarios()


def test_there_are_scenarios_to_check() -> None:
    assert len(SCENARIOS) >= 20
    assert any(s.decidable_from_sql for s in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario(scenario: SqlScenario, frozen_clock: FrozenClock) -> None:
    result = classify(scenario.sql, now=frozen_clock.now())
    pass_one = evaluate_pass_one(POLICY, result)
    expected = set(scenario.expected_rules)
    if scenario.decidable_from_sql:
        assert pass_one.route == Route.BLOCK, f"{scenario.id}: {result}"
        matched_blocks = {m.name for m in pass_one.matched if m.outcome == "block"}
        assert matched_blocks == expected, f"{scenario.id}: {result}"
    else:
        assert pass_one.route != Route.BLOCK, f"{scenario.id} must not be blocked in pass 1"
        assert (pass_one.route == Route.FAST_LANE) == (FAST_LANE_NOTE in expected), scenario.id


def test_terminate_backends_is_a_write_with_its_function_recorded(
    frozen_clock: FrozenClock,
) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "terminate-backends")
    result = classify(scenario.sql, now=frozen_clock.now())
    assert result.kind == Kind.WRITE
    assert result.side_effect_functions == ("pg_terminate_backend",)


def test_the_cte_delete_is_a_write(frozen_clock: FrozenClock) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "cte-delete-free-users")
    assert classify(scenario.sql, now=frozen_clock.now()).kind == Kind.WRITE


def test_the_replit_delete_is_pinned_to_the_anchor(frozen_clock: FrozenClock) -> None:
    scenario = next(s for s in SCENARIOS if s.id == "replit-clean-up-inactive-users")
    result = classify(scenario.sql, now=frozen_clock.now())
    assert result.pinned_values == {"now()": "2026-10-01T00:00:00Z"}
    assert result.target_tables == ("users",)
    assert result.has_where is True
