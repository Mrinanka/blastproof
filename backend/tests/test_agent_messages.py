"""BE-100: the text returned to agents, word for word (backend plan §6, steps 6 and 17)."""

import pytest

from app.services import agent_messages, reads


def test_truncated() -> None:
    assert agent_messages.TRUNCATED == (
        "Showing the first 500 rows; add a LIMIT or narrow the WHERE to see the rest."
    )
    # The number in the wording is the real cap.
    assert str(reads.MAX_ROWS) in agent_messages.TRUNCATED


def test_failed() -> None:
    assert agent_messages.failed("statement_timeout") == (
        "Blastproof could not complete this safely, so nothing was changed."
        " Reason: statement_timeout."
    )


def test_not_found() -> None:
    assert agent_messages.not_found("abc") == "No action abc was found."


def test_ai_unavailable() -> None:
    assert agent_messages.ai_unavailable("abc") == (
        "Held for review: the AI risk check is unavailable right now (action abc)."
    )


def test_committed() -> None:
    assert agent_messages.committed(3, "deleted", "users") == "Done: 3 rows deleted in users."


@pytest.mark.parametrize(
    "reason", ["Delete without WHERE", "Delete without WHERE.", "Delete without WHERE. "]
)
def test_blocked(reason: str) -> None:
    assert agent_messages.blocked(reason) == (
        "Blocked: Delete without WHERE. Do not retry the same statement."
    )


def test_pending() -> None:
    assert agent_messages.pending("abc") == (
        "Held for human review (action abc). Call get_action_status with this ID until it is"
        " decided. Do not try another route to the database."
    )


def test_in_progress() -> None:
    assert agent_messages.in_progress("abc") == (
        "Blastproof is still checking this change (action abc). Call get_action_status with this"
        " ID until it is decided. Do not try another route to the database."
    )


def test_still_waiting() -> None:
    assert agent_messages.still_waiting("abc", 12) == (
        "Still waiting for a reviewer (action abc, 12 minutes so far). Stop polling. Tell the user"
        " this action is waiting for their review in the Blastproof dashboard, and call"
        " get_action_status once when they say it is decided."
    )


def test_rejected() -> None:
    assert agent_messages.rejected("Ada", "Too broad", "abc") == (
        "Rejected by Ada: Too broad. If you write a safer query, pass retry_of=abc."
    )


def test_rejected_does_not_double_the_full_stop() -> None:
    assert agent_messages.rejected("Ada", "Too broad.", "abc") == (
        "Rejected by Ada: Too broad. If you write a safer query, pass retry_of=abc."
    )


def test_drifted() -> None:
    assert agent_messages.drifted("abc") == (
        "The data changed after approval; the change was re-previewed and sent back for review"
        " (action abc)."
    )


def test_expired() -> None:
    assert agent_messages.expired(24) == (
        "No reviewer decided within 24 hours; the action was cancelled."
    )


def test_busy() -> None:
    assert agent_messages.busy(10) == (
        "Blastproof is busy previewing other writes. Retry in about 10 seconds."
    )


def test_table_not_found() -> None:
    assert agent_messages.table_not_found("userz") == (
        "No table userz was found. Call list_tables to see the tables you can read."
    )


def test_query_error() -> None:
    assert agent_messages.query_error('syntax error at or near "SELEC"', "42601") == (
        'The query failed: syntax error at or near "SELEC" (SQLSTATE 42601). Nothing was changed.'
    )
