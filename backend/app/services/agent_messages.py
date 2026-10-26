"""The exact text returned to agents for each outcome (backend plan §6, BE-100).

Tested word for word. Step 6 has the read-side templates; step 17 adds the write outcomes
(committed, blocked, pending, still_waiting, rejected, drifted, expired, busy, ai_unavailable).
`not_supported`, `table_not_found` and `query_error` exist only until the write pipeline and
knowledge arrive (D-103, D-104).
"""

# Matches reads.MAX_ROWS; the number is part of the wording, so it is written out.
TRUNCATED = "Showing the first 500 rows; add a LIMIT or narrow the WHERE to see the rest."


def failed(reason_code: str) -> str:
    return (
        f"Blastproof could not complete this safely, so nothing was changed. Reason: {reason_code}."
    )


def not_found(action_id: str) -> str:
    return f"No action {action_id} was found."


def ai_unavailable(action_id: str) -> str:
    return f"Held for review: the AI risk check is unavailable right now (action {action_id})."


def not_supported() -> str:
    return (
        "Blastproof can only run a single SELECT for now; writes are not supported yet."
        " Nothing was changed."
    )


def table_not_found(name: str) -> str:
    return f"No table {name} was found. Call list_tables to see the tables you can read."


def query_error(message: str, sqlstate: str) -> str:
    """Postgres's primary message, for the agent only: never logged or stored (D-104)."""
    return f"The query failed: {message} (SQLSTATE {sqlstate}). Nothing was changed."
