"""Step 9 "Done when": every SQL scenario through pass 1, and every incident decidable from SQL
blocked there (DW-B09)."""

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.services.classifier import classify
from app.services.policy import Route, evaluate_pass_one, load_policy
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import SCENARIOS_DIR

POLICY = load_policy(DEFAULT_POLICY_FILE)


def scenarios(file: str) -> list[SqlScenario]:
    parsed = load_scenario_file(SCENARIOS_DIR / file).scenarios
    return [s for s in parsed if isinstance(s, SqlScenario)]


INCIDENTS = scenarios("incidents.yaml")
ALL = INCIDENTS + scenarios("labeled.yaml")


@pytest.mark.parametrize("scenario", ALL, ids=lambda s: s.id)
def test_pass_one_on_every_scenario(scenario: SqlScenario, frozen_clock: FrozenClock) -> None:
    result = evaluate_pass_one(POLICY, classify(scenario.sql, now=frozen_clock.now()))
    if scenario.decidable_from_sql:
        assert result.route == Route.BLOCK
        blocking = [m.name for m in result.matched if m.outcome == "block"]
        assert sorted(blocking) == sorted(scenario.expected_rules)
    else:
        assert result.route != Route.BLOCK
        wants_fast_lane = "Fast-lane note" in scenario.expected_rules
        assert (result.route == Route.FAST_LANE) is wants_fast_lane


def test_every_incident_decidable_from_sql_is_blocked_in_pass_one(
    frozen_clock: FrozenClock,
) -> None:
    decidable = [s for s in INCIDENTS if s.decidable_from_sql]
    assert decidable, "the incident file should hold at least one SQL-decidable replay"
    for scenario in decidable:
        result = evaluate_pass_one(POLICY, classify(scenario.sql, now=frozen_clock.now()))
        assert result.route == Route.BLOCK, scenario.id
        assert result.reason.strip(), scenario.id


def test_the_replit_delete_is_not_decided_by_sql_alone(frozen_clock: FrozenClock) -> None:
    replit = next(s for s in INCIDENTS if s.id == "replit-clean-up-inactive-users")
    result = evaluate_pass_one(POLICY, classify(replit.sql, now=frozen_clock.now()))
    assert result.route == Route.PREVIEW
    assert {"Most of a table", "Large change", "Touches paying customers"} <= set(result.skipped)


def test_terminate_backends_is_blocked_by_the_new_rule(frozen_clock: FrozenClock) -> None:
    scenario = next(s for s in ALL if s.id == "terminate-backends")
    result = evaluate_pass_one(POLICY, classify(scenario.sql, now=frozen_clock.now()))
    assert result.route == Route.BLOCK
    assert [m.name for m in result.matched] == ["No side-effect functions"]
