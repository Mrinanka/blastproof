"""Step 17: what the agent is told, from stored state (backend plan §6). Pure code, no Docker."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from app.services import agent_messages
from app.services.agent_outcomes import AgentView, outcome_for

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AID = str(ID)
SETTINGS = Settings(_env_file=None, app_env="test")
IMPACT = {
    "table_changes": {
        "users": {"deleted": 7, "updated": 0, "inserted": 0},
        "orders": {"deleted": 20, "updated": 0, "inserted": 0},
    },
    "cascade_tables": ["orders"],
}


def view(status: str, **fields: Any) -> AgentView:
    base = AgentView(
        id=ID,
        status=status,
        decision=None,
        policy_reason=None,
        reviewer_note=None,
        decided_by_name=None,
        failure_reason=None,
        created_at=NOW - timedelta(seconds=5),
        statement_type="delete",
        tables=["orders", "users"],
        drifts=0,
        ai_unavailable=False,
        committed_rows=None,
        impact=IMPACT,
    )
    return replace(base, **fields)


def say(v: AgentView, *, polling: bool = False, now: datetime = NOW) -> str | None:
    return outcome_for(v, now=now, settings=SETTINGS, polling=polling).message


# --- One row per stored state -------------------------------------------------------------------


def test_committed_uses_the_committed_row_count() -> None:
    assert say(view("committed", committed_rows=3)) == "Done: 3 rows deleted in users."


@pytest.mark.parametrize(
    ("statement_type", "counts", "expected"),
    [
        ("delete", {"deleted": 7, "updated": 1, "inserted": 2}, "Done: 7 rows deleted in users."),
        ("update", {"deleted": 7, "updated": 1, "inserted": 2}, "Done: 1 rows updated in users."),
        ("insert", {"deleted": 7, "updated": 1, "inserted": 2}, "Done: 2 rows inserted in users."),
        ("merge", {"deleted": 7, "updated": 1, "inserted": 2}, "Done: 10 rows changed in users."),
        ("truncate", {"deleted": 7, "updated": 0, "inserted": 0}, "Done: 7 rows changed in users."),
    ],
)
def test_committed_stub_rows_fall_back_to_the_impact_by_verb(
    statement_type: str, counts: dict[str, int], expected: str
) -> None:
    impact = {"table_changes": {"users": counts}, "cascade_tables": []}
    v = view("committed", statement_type=statement_type, tables=["users"], impact=impact)
    assert say(v) == expected


def test_a_cascade_table_is_never_named_as_the_target() -> None:
    # `orders` is listed first and has the bigger count, but it was only cascaded into.
    assert say(view("committed", tables=["orders", "users"])) == "Done: 7 rows deleted in users."


def test_committed_without_impact_still_answers() -> None:
    v = view("committed", impact=None, tables=[], committed_rows=None)
    assert say(v) == "Done: 0 rows deleted in the company database."


def test_blocked_names_the_reason_without_doubling_the_full_stop() -> None:
    expected = "Blocked: Delete without WHERE. Do not retry the same statement."
    assert say(view("blocked", policy_reason="Delete without WHERE")) == expected
    assert say(view("blocked", policy_reason="Delete without WHERE.")) == expected
    assert ".." not in (say(view("blocked", policy_reason="No WHERE...")) or "")


def test_rejected_names_the_reviewer_and_the_note() -> None:
    v = view("rejected", decided_by_name="Ada", reviewer_note="Too broad")
    assert say(v) == agent_messages.rejected("Ada", "Too broad", AID)


def test_expired_names_the_configured_hours() -> None:
    assert say(view("expired")) == agent_messages.expired(SETTINGS.review_timeout_hours)


def test_executed_has_no_message() -> None:
    assert say(view("executed")) is None


@pytest.mark.parametrize(
    "status", ["received", "classified", "previewing", "approved", "committing", "drifted"]
)
def test_work_in_flight_is_in_progress(status: str) -> None:
    assert say(view(status)) == agent_messages.in_progress(AID)


# --- Failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("shadow_busy", agent_messages.busy(10)),
        ("drifted", agent_messages.failed("drifted")),
        ("shadow_timeout", agent_messages.failed("shadow_timeout")),
        ("unknown_table", agent_messages.failed("unknown_table")),
        ("workflow_error", agent_messages.failed("workflow_error")),
        ("commit_statement_error_23503", agent_messages.failed("commit_statement_error_23503")),
        ("query_error_42P01", agent_messages.failed("query_error_42P01")),
        ("company_db_unavailable", agent_messages.failed("company_db_unavailable")),
        (None, agent_messages.failed("internal_error")),
    ],
)
def test_failures(reason: str | None, expected: str) -> None:
    assert say(view("failed", failure_reason=reason)) == expected


def test_a_failed_describe_rebuilds_its_message_from_the_table() -> None:
    v = view("failed", failure_reason="table_not_found", tables=["userz"])
    assert say(v) == agent_messages.table_not_found("userz")


# --- awaiting_review ----------------------------------------------------------------------------


def waiting(**fields: Any) -> AgentView:
    return view("awaiting_review", **fields)


def test_a_review_is_pending() -> None:
    assert say(waiting()) == agent_messages.pending(AID)


def test_drift_beats_ai_unavailable_beats_pending() -> None:
    keep = agent_messages.KEEP_POLLING
    assert say(waiting(ai_unavailable=True)) == f"{agent_messages.ai_unavailable(AID)} {keep}"
    assert say(waiting(drifts=1)) == f"{agent_messages.drifted(AID)} {keep}"
    both = say(waiting(drifts=1, ai_unavailable=True))
    assert both == f"{agent_messages.drifted(AID)} {keep}"


def test_still_waiting_starts_exactly_at_the_budget_and_only_when_polling() -> None:
    budget = timedelta(minutes=SETTINGS.agent_poll_budget_minutes)
    just_under = waiting(created_at=NOW - budget + timedelta(seconds=1))
    at_budget = waiting(created_at=NOW - budget)
    assert say(just_under, polling=True) == agent_messages.pending(AID)
    assert say(at_budget, polling=True) == agent_messages.still_waiting(
        AID, SETTINGS.agent_poll_budget_minutes
    )
    # query_database never gives still_waiting, however old the action is.
    assert say(at_budget, polling=False) == agent_messages.pending(AID)


def test_still_waiting_counts_whole_minutes() -> None:
    old = waiting(created_at=NOW - timedelta(minutes=37, seconds=59))
    assert say(old, polling=True) == agent_messages.still_waiting(AID, 37)


def test_the_budget_applies_to_reviews_only() -> None:
    old = NOW - timedelta(hours=2)
    assert say(view("previewing", created_at=old), polling=True) == agent_messages.in_progress(AID)
    assert say(view("committed", created_at=old, committed_rows=1), polling=True) == (
        "Done: 1 rows deleted in users."
    )
