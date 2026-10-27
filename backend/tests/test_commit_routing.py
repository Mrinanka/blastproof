"""Step 16: the graph's routing after a commit, the drift rule, and what passes the fail-closed
wrapper. No database."""

import uuid
from typing import Any

import pytest
from langgraph.graph import END

from app.db.enums import Decision
from app.services.commit import CommitOutcomeUnknownError
from app.workflow.graph import _Nodes, after_drift, route_after_commit


def test_a_drift_sends_the_run_back_to_the_preview() -> None:
    assert route_after_commit({"repreview": True, "drifts": 1}) == "shadow"


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"repreview": False},
        {"repreview": False, "drifts": 1},  # committed after an earlier drift
        {"repreview": True, "outcome": "failed"},
        {"outcome": "mismatch"},
    ],
)
def test_anything_else_ends_the_run(state: dict[str, Any]) -> None:
    assert route_after_commit(state) == END  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("decision", "drifts", "expected"),
    [
        (Decision.ALLOW, 0, Decision.ALLOW),
        (Decision.ALLOW, 1, Decision.REVIEW),
        (Decision.ALLOW, 2, Decision.REVIEW),
        (Decision.REVIEW, 0, Decision.REVIEW),
        (Decision.REVIEW, 1, Decision.REVIEW),
        (Decision.BLOCK, 0, Decision.BLOCK),
        (Decision.BLOCK, 1, Decision.BLOCK),  # a drift never loosens a block
    ],
)
def test_after_a_drift_an_allow_becomes_a_review_and_nothing_gets_looser(
    decision: Decision, drifts: int, expected: Decision
) -> None:
    result, reason = after_drift(decision, "Small insert.", drifts)
    assert result == expected
    assert ("Small insert." in reason) and (
        (decision == Decision.ALLOW and drifts > 0) == ("changed after" in reason)
    )


async def test_an_unknown_commit_outcome_passes_through_the_fail_closed_wrapper() -> None:
    async def node(_state: Any) -> dict[str, Any]:
        raise CommitOutcomeUnknownError

    wrapped = _Nodes(None).guarded("commit", node)  # type: ignore[arg-type]
    with pytest.raises(CommitOutcomeUnknownError):
        await wrapped({"action_id": str(uuid.uuid4())})  # type: ignore[typeddict-item]
