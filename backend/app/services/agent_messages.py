"""The exact text returned to agents for each outcome (backend plan §6, BE-100).

Tested word for word. Step 6 has the read-side templates; step 17 adds the write outcomes
(committed, blocked, pending, in_progress, still_waiting, rejected, drifted, expired, busy).
`table_not_found` and `query_error` exist only until knowledge arrives (D-104).
"""

# Matches reads.MAX_ROWS; the number is part of the wording, so it is written out.
TRUNCATED = "Showing the first 500 rows; add a LIMIT or narrow the WHERE to see the rest."

# The instruction every "held" message ends with, so an agent never stops at "held for review".
KEEP_POLLING = (
    "Call get_action_status with this ID until it is decided."
    " Do not try another route to the database."
)


def failed(reason_code: str) -> str:
    return (
        f"Blastproof could not complete this safely, so nothing was changed. Reason: {reason_code}."
    )


def not_found(action_id: str) -> str:
    return f"No action {action_id} was found."


def ai_unavailable(action_id: str) -> str:
    return f"Held for review: the AI risk check is unavailable right now (action {action_id})."


def committed(rows: int, verb: str, table: str) -> str:
    return f"Done: {rows} rows {verb} in {table}."


def blocked(reason: str) -> str:
    # Rules end their reasons with a full stop or not; the template adds exactly one.
    return f"Blocked: {reason.rstrip('. ')}. Do not retry the same statement."


def pending(action_id: str) -> str:
    return f"Held for human review (action {action_id}). {KEEP_POLLING}"


def in_progress(action_id: str) -> str:
    return f"Blastproof is still checking this change (action {action_id}). {KEEP_POLLING}"


def still_waiting(action_id: str, minutes: int) -> str:
    return (
        f"Still waiting for a reviewer (action {action_id}, {minutes} minutes so far)."
        " Stop polling. Tell the user this action is waiting for their review in the Blastproof"
        " dashboard, and call get_action_status once when they say it is decided."
    )


def rejected(reviewer: str, note: str, action_id: str) -> str:
    # A note that already ends in a full stop is not given a second one.
    return (
        f"Rejected by {reviewer}: {note.rstrip('. ')}."
        f" If you write a safer query, pass retry_of={action_id}."
    )


def drifted(action_id: str) -> str:
    return (
        "The data changed after approval; the change was re-previewed and sent back for review"
        f" (action {action_id})."
    )


def expired(hours: int) -> str:
    return f"No reviewer decided within {hours} hours; the action was cancelled."


def busy(seconds: int) -> str:
    return f"Blastproof is busy previewing other writes. Retry in about {seconds} seconds."


def table_not_found(name: str) -> str:
    return f"No table {name} was found. Call list_tables to see the tables you can read."


def query_error(message: str, sqlstate: str) -> str:
    """Postgres's primary message, for the agent only: never logged or stored (D-104)."""
    return f"The query failed: {message} (SQLSTATE {sqlstate}). Nothing was changed."
