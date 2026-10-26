"""BE-100: the text returned to agents, word for word (backend plan §6, step 6 templates)."""

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


def test_not_supported() -> None:
    assert agent_messages.not_supported() == (
        "Blastproof can only run a single SELECT for now; writes are not supported yet."
        " Nothing was changed."
    )


def test_table_not_found() -> None:
    assert agent_messages.table_not_found("userz") == (
        "No table userz was found. Call list_tables to see the tables you can read."
    )


def test_query_error() -> None:
    assert agent_messages.query_error('syntax error at or near "SELEC"', "42601") == (
        'The query failed: syntax error at or near "SELEC" (SQLSTATE 42601). Nothing was changed.'
    )
