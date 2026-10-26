"""The graph's routing functions, over their full input tables (no database)."""

from decimal import Decimal

import pytest
from langgraph.graph import END

from app.db.enums import Decision
from app.services.policy import Route
from app.workflow.graph import (
    next_unless_ended,
    over_cost_cap,
    route_after_decide,
    route_after_human_review,
    route_after_post_rules,
    route_after_pre_rules,
)
from app.workflow.state import WriteState


def test_next_unless_ended() -> None:
    route = next_unless_ended("next")
    assert route({"action_id": "x"}) == "next"
    assert route({"action_id": "x", "outcome": "failed"}) == END
    assert route({"action_id": "x", "outcome": "mismatch"}) == END


@pytest.mark.parametrize(
    ("route", "expected"),
    [(Route.BLOCK, "block"), (Route.FAST_LANE, "fast_lane"), (Route.PREVIEW, "shadow")],
)
def test_after_pre_rules(route: Route, expected: str) -> None:
    assert route_after_pre_rules({"route": route.value}) == expected


def test_after_pre_rules_ends_when_failed() -> None:
    assert route_after_pre_rules({"route": "preview", "outcome": "failed"}) == END


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("block", "block"), ("review", "retrieve"), ("allow", "retrieve")],
)
def test_after_post_rules(decision: str, expected: str) -> None:
    state: WriteState = {"post_decision": {"decision": decision, "reason": "r"}}
    assert route_after_post_rules(state) == expected


@pytest.mark.parametrize(
    ("decision", "expected"),
    [(Decision.ALLOW, "approve"), (Decision.REVIEW, "request_review"), (Decision.BLOCK, "block")],
)
def test_after_decide(decision: Decision, expected: str) -> None:
    assert route_after_decide({"decision": decision.value}) == expected


@pytest.mark.parametrize(
    ("review", "expected"),
    [
        ({"approved": True}, "commit"),
        ({"approved": False}, END),
        ({}, END),
        (None, END),
        ({"approved": 1}, END),  # a truthy look-alike
        ({"approved": "true"}, END),
        ({"approved": True, "extra": 1}, END),
        ("approved", END),
        (True, END),
    ],
)
def test_after_human_review_commits_only_on_an_exact_approval(
    review: object, expected: str
) -> None:
    state: WriteState = {} if review is None else {"review": review}  # type: ignore[typeddict-item]
    assert route_after_human_review(state) == expected


@pytest.mark.parametrize(
    ("cost", "over"),
    [
        (None, True),  # unknown counts as over (BE-70)
        (Decimal("0"), False),
        (Decimal("0.099999"), False),
        (Decimal("0.10"), False),  # equal to the cap is not over
        (Decimal("0.100001"), True),
    ],
)
def test_cost_cap(cost: Decimal | None, over: bool) -> None:
    assert over_cost_cap(cost, Decimal("0.10")) is over


def test_a_zero_cap_is_over_for_any_known_cost_above_zero() -> None:
    assert over_cost_cap(Decimal("0"), Decimal("0")) is False
    assert over_cost_cap(Decimal("0.000001"), Decimal("0")) is True
