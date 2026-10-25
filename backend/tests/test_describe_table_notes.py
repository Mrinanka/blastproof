"""Step 11: describe_table returns the notes written about a table (BE-18, knowledge half)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.knowledge import SCHEMA_DOCS_SOURCE, SCHEMA_SCAN_SOURCE, split_document
from tests.conftest import (
    DEMO_COMPANY_DIR,
    AuthDb,
    bearer,
    call_tool,
    mcp_call,
)
from tests.conftest import (
    TestAgent as McpAgent,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]


@pytest.fixture
def agent(auth_db: AuthDb) -> McpAgent:
    return auth_db.make_agent(auth_db.make_user().id)


@pytest.fixture
def notes_db(auth_db: AuthDb):  # type: ignore[no-untyped-def]
    """schema_docs.md and a scan-like chunk for users, inserted directly (no vectors needed)."""
    auth_db.execute("DELETE FROM knowledge_chunks")
    docs = split_document(
        SCHEMA_DOCS_SOURCE, Path(DEMO_COMPANY_DIR / "schema_docs.md").read_bytes()
    )
    rows = [(d.source, list(d.table_names), d.content) for d in docs]
    rows.append((SCHEMA_SCAN_SOURCE, ["users"], "## table: users\n\n- id: bigint, not null"))
    for row in rows:
        auth_db.execute(
            "INSERT INTO knowledge_chunks (source, table_names, content) VALUES (%s, %s, %s)", row
        )
    yield auth_db
    auth_db.execute("DELETE FROM knowledge_chunks")


def test_users_returns_the_schema_docs_note_and_not_the_scan_chunk(
    mcp_client: TestClient, agent: McpAgent, notes_db: AuthDb
) -> None:
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": "users"})
    assert result["status"] == "executed"
    [note] = result["notes"]
    assert note["source"] == SCHEMA_DOCS_SOURCE
    assert "Deleting a user also deletes their orders" in note["text"]
    assert "id: bigint" not in note["text"]
    assert result["columns"]  # the columns are still returned


def test_the_note_is_found_through_a_schema_qualified_name(
    mcp_client: TestClient, agent: McpAgent, notes_db: AuthDb
) -> None:
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": "public.users"})
    assert len(result["notes"]) == 1


def test_a_table_with_no_notes_gives_an_empty_list(
    mcp_client: TestClient, agent: McpAgent, auth_db: AuthDb
) -> None:
    auth_db.execute("DELETE FROM knowledge_chunks")
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": "products"})
    assert result["status"] == "executed"
    assert result["notes"] == []


def test_a_note_about_two_tables_shows_on_both(
    mcp_client: TestClient, agent: McpAgent, auth_db: AuthDb
) -> None:
    auth_db.execute("DELETE FROM knowledge_chunks")
    auth_db.execute(
        "INSERT INTO knowledge_chunks (source, table_names, content)"
        " VALUES ('upload.md', ARRAY['orders', 'payments'], 'Refunds touch both tables.')"
    )
    try:
        for table in ("orders", "payments"):
            result = call_tool(mcp_client, agent.key, "describe_table", {"table": table})
            assert [n["text"] for n in result["notes"]] == ["Refunds touch both tables."]
        other = call_tool(mcp_client, agent.key, "describe_table", {"table": "users"})
        assert other["notes"] == []
    finally:
        auth_db.execute("DELETE FROM knowledge_chunks")


def test_a_failed_describe_has_no_notes(
    mcp_client: TestClient, agent: McpAgent, notes_db: AuthDb
) -> None:
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": "userz"})
    assert result["status"] == "failed"
    assert result["notes"] == []


def test_a_wrong_key_still_gets_401(mcp_client: TestClient, notes_db: AuthDb) -> None:
    body, headers = mcp_call("describe_table", {"table": "users"})
    response = mcp_client.post("/mcp", json=body, headers=headers | bearer("bp_wrong"))
    assert response.status_code == 401


def test_an_inactive_agent_still_gets_401(
    mcp_client: TestClient, auth_db: AuthDb, notes_db: AuthDb
) -> None:
    inactive = auth_db.make_agent(auth_db.make_user().id, is_active=False)
    body, headers = mcp_call("describe_table", {"table": "users"})
    response = mcp_client.post("/mcp", json=body, headers=headers | bearer(inactive.key))
    assert response.status_code == 401
