"""`facts_equal`: do the facts a preview measured equal a scenario's labeled `expected_impact`?"""

from typing import Any

import pytest

from evals.live_mode import facts_equal
from evals.schema import SqlScenario


def scenario(impact: dict[str, Any] | None) -> SqlScenario:
    return SqlScenario.model_validate(
        {
            "kind": "sql",
            "id": "s",
            "intent": "i",
            "sql": "DELETE FROM users WHERE id = 1",
            "expected_decision": "review",
            "decidable_from_sql": False,
            "expected_impact": impact,
        }
    )


LABEL = {
    "table_changes": {"users": {"deleted": 2}, "orders": {"deleted": 5}},
    "breakdowns": {"users.plan": {"pro": 2}},
    "derived_groups": {"users.paying": 2},
}


def measured(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "table_changes": {
            "users": {"deleted": 2, "updated": 0, "inserted": 0},
            "orders": {"deleted": 5, "updated": 0, "inserted": 0},
        },
        "breakdowns": {"users.plan": {"pro": 2}},
        "derived_groups": {"users.paying": 2},
        "rows_before": {"users": 50000},
        "samples": [],
    }
    return base | changes


def test_equal_facts_match_even_with_zero_counts_spelled_out() -> None:
    assert facts_equal(scenario(LABEL), measured()) is True


def test_a_different_count_does_not_match() -> None:
    other = {"users": {"deleted": 3, "updated": 0, "inserted": 0}}
    assert facts_equal(scenario(LABEL), measured(table_changes=other)) is False


def test_an_extra_changed_table_does_not_match() -> None:
    extra = measured()["table_changes"] | {"invoices": {"deleted": 0, "updated": 1, "inserted": 0}}
    assert facts_equal(scenario(LABEL), measured(table_changes=extra)) is False


def test_a_missing_changed_table_does_not_match() -> None:
    fewer = {"users": {"deleted": 2, "updated": 0, "inserted": 0}}
    assert facts_equal(scenario(LABEL), measured(table_changes=fewer)) is False


def test_a_different_breakdown_does_not_match() -> None:
    assert facts_equal(scenario(LABEL), measured(breakdowns={"users.plan": {"pro": 1}})) is False


def test_a_different_derived_group_does_not_match() -> None:
    assert facts_equal(scenario(LABEL), measured(derived_groups={"users.paying": 1})) is False


@pytest.mark.parametrize("impact", [None, {}])
def test_nothing_to_compare_is_none(impact: dict[str, Any] | None) -> None:
    assert facts_equal(scenario(LABEL), impact) is None
    assert facts_equal(scenario(None), measured()) is None
