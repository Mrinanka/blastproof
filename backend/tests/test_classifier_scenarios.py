"""Step 8 "Done when": the classifier-relevant incident scenarios pass.

The classifier only supplies facts; the rules that read them arrive in step 9. Until then this
file holds a local mirror of backend plan §9's SQL-only rules. Step 9 switches the mirror to
`policies/rules.yaml`, as `DERIVED_GROUPS` in `test_seed_counts.py` will.
"""

from collections.abc import Callable

import pytest

from app.core.clock import FrozenClock
from app.services.classifier import Classification, Kind, classify
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import SCENARIOS_DIR

# name -> (condition on the classification, outcome). Only rules that read SQL alone; the diff
# rules (rows affected, fraction of table, derived groups, cascades) need the shadow run.
SqlRule = Callable[[Classification], bool]
SQL_ONLY_BLOCK_RULES: dict[str, SqlRule] = {
    "No DDL": lambda c: c.kind == Kind.DDL,
    "Unparseable SQL": lambda c: c.kind == Kind.UNKNOWN,
    "No DROP or TRUNCATE": lambda c: bool({"drop", "truncate"} & set(c.statement_types)),
    "One statement only": lambda c: c.multi_statement,
    "No transaction control": lambda c: c.transaction_control,
    "Hands off Blastproof's schema": lambda c: "blastproof" in c.schemas,
    "Volatile values in WHERE": lambda c: c.volatile_in_where,
    "Unordered LIMIT": lambda c: c.unordered_limit,
    "Delete or update without WHERE": lambda c: (
        bool({"delete", "update"} & set(c.statement_types)) and not c.has_where
    ),
}
FAST_LANE_NOTE = "Fast-lane note"


def fast_lane_note_matches(c: Classification) -> bool:
    return c.fast_lane_eligible and "customer_notes" in c.tables


def sql_scenarios() -> list[SqlScenario]:
    found: list[SqlScenario] = []
    for name in ("incidents.yaml", "labeled.yaml"):
        for scenario in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if isinstance(scenario, SqlScenario):
                found.append(scenario)
    return found


SCENARIOS = sql_scenarios()


def matched_block_rules(result: Classification) -> set[str]:
    return {name for name, rule in SQL_ONLY_BLOCK_RULES.items() if rule(result)}


def test_there_are_scenarios_to_check() -> None:
    assert len(SCENARIOS) >= 20
    assert any(s.decidable_from_sql for s in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario(scenario: SqlScenario, frozen_clock: FrozenClock) -> None:
    result = classify(scenario.sql, now=frozen_clock.now())
    matched = matched_block_rules(result)
    expected = set(scenario.expected_rules)
    if scenario.decidable_from_sql:
        assert matched == expected, f"{scenario.id}: {result}"
    else:
        assert matched == set(), f"{scenario.id} must not be blocked in pass 1: {matched}"
        assert fast_lane_note_matches(result) == (FAST_LANE_NOTE in expected), scenario.id


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
