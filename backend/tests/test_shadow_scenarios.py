"""EVAL-05: every scenario with `expected_impact`, measured on a real copy.

The measured diff must equal the scenario's expected counts, and pass 2 on the measured facts must
give the decision `test_policy_pass_two` expects from the expected counts.
"""

import uuid

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.db.enums import Decision
from app.services.classifier import classify
from app.services.policy import Route, evaluate_pass_one, evaluate_pass_two, load_policy
from evals.schema import SqlScenario
from tests.conftest import fake_neon_client, shadow_runner
from tests.test_policy_pass_two import RULES_ONLY_OUTCOME, measured

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

POLICY = load_policy(DEFAULT_POLICY_FILE)


@pytest.mark.parametrize("scenario", measured(), ids=lambda s: s.id)
async def test_the_measured_diff_equals_the_expected_impact(
    scenario: SqlScenario, frozen_clock: FrozenClock
) -> None:
    expected = scenario.expected_impact
    assert expected is not None
    classification = classify(scenario.sql, now=frozen_clock.now())
    runner = shadow_runner({"local": fake_neon_client(frozen_clock)})
    diff = (
        await runner.run(action_id=uuid.uuid4(), profile="local", classification=classification)
    ).diff

    measured_changes = {
        name: {
            k: v
            for k, v in (("deleted", t.deleted), ("updated", t.updated), ("inserted", t.inserted))
            if v
        }
        for name, t in diff.tables.items()
        if t.changed
    }
    assert measured_changes == {
        t: c.model_dump(exclude_defaults=True) for t, c in expected.table_changes.items()
    }
    assert diff.breakdowns == expected.breakdowns
    assert diff.derived_groups == expected.derived_groups

    if evaluate_pass_one(POLICY, classification).route == Route.BLOCK:
        return
    decision = evaluate_pass_two(POLICY, classification, diff.facts())
    if "Fast-lane note" in scenario.expected_rules:
        return  # the fast lane never reaches pass 2
    assert {m.name for m in decision.matched} == set(scenario.expected_rules), scenario.id
    expected_decision = RULES_ONLY_OUTCOME.get(scenario.id, Decision(scenario.expected_decision))
    assert decision.decision == expected_decision, scenario.id
