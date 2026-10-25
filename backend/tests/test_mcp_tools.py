"""Step 6: the four MCP tools against the local company_main (BE-17 to BE-22, ARCH-13, DW-B06).

Reads run as blastproof_reader; every query_database, list_tables and describe_table call is
recorded under the agent owner's user_id; get_action_status records nothing.
"""

import time

import asyncpg
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.logging import configure_logging
from app.main import create_app
from app.services import agent_messages, reads
from app.services.company_db import SERVER_SETTINGS
from tests.conftest import (
    COMPANY_READER_URL,
    MCP_BASE_URL,
    AuthDb,
    bearer,
    call_tool,
    mcp_call,
    mcp_settings,
    parse_rpc,
)
from tests.conftest import (
    TestAgent as McpAgent,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

COMPANY_TABLES = [
    "customer_notes",
    "feature_flags",
    "invoices",
    "order_items",
    "orders",
    "organizations",
    "payments",
    "products",
    "subscriptions",
    "support_tickets",
    "users",
]


@pytest.fixture
def agent(auth_db: AuthDb) -> McpAgent:
    return auth_db.make_agent(auth_db.make_user().id)


def query(client: TestClient, agent: McpAgent, sql: str, intent: str = "test") -> dict:
    return call_tool(client, agent.key, "query_database", {"sql": sql, "intent": intent})


def history(auth_db: AuthDb, action_id: str) -> list[str]:
    rows = auth_db.execute(
        "SELECT event_type FROM audit_events WHERE action_id = %s ORDER BY created_at",
        (action_id,),
    )
    return [event_type for (event_type,) in rows]


def action_row(auth_db: AuthDb, action_id: str) -> dict:
    columns = (
        "user_id, agent_id, system_id, session_id, tool, intent, sql, pinned_sql, sql_display,"
        " statement_type, tables, status, decision, failure_reason, total_ms"
    )
    [row] = auth_db.execute(f"SELECT {columns} FROM actions WHERE id = %s", (action_id,))
    return dict(zip([c.strip() for c in columns.split(",")], row, strict=True))


def company_count(sql: str) -> int:
    with psycopg.connect(COMPANY_READER_URL) as conn:
        [(count,)] = conn.execute(sql).fetchall()
    return count


# --- list_tables and describe_table ------------------------------------------------------------


def test_list_tables_returns_the_public_tables_with_descriptions(
    mcp_client: TestClient, agent: McpAgent
) -> None:
    result = call_tool(mcp_client, agent.key, "list_tables")
    assert result["status"] == "executed"
    assert [t["name"] for t in result["tables"]] == COMPANY_TABLES
    assert all(t["description"] for t in result["tables"])
    users = next(t for t in result["tables"] if t["name"] == "users")
    assert "plan" in users["description"]
    # The blastproof schema (applied_actions, seed_info) is never listed.
    assert "blastproof" not in str(result)
    assert "applied_actions" not in str(result)


def test_describe_table_returns_columns_and_types(mcp_client: TestClient, agent: McpAgent) -> None:
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": "users"})
    assert result["status"] == "executed"
    assert result["table"] == "users"
    assert result["description"]
    columns = {c["name"]: (c["type"], c["nullable"]) for c in result["columns"]}
    assert list(columns)[:3] == ["id", "organization_id", "email"]
    assert columns["id"] == ("bigint", False)
    assert columns["organization_id"] == ("bigint", True)
    assert columns["plan"] == ("text", False)
    assert columns["last_login_at"] == ("timestamp with time zone", False)
    # "public." is accepted too.
    qualified = call_tool(mcp_client, agent.key, "describe_table", {"table": "public.users"})
    assert qualified["columns"] == result["columns"]


@pytest.mark.parametrize(
    "name", ["userz", "blastproof.applied_actions", "applied_actions", "pg_class", "users; --"]
)
def test_describe_table_refuses_unknown_and_hidden_tables(
    mcp_client: TestClient, agent: McpAgent, auth_db: AuthDb, name: str
) -> None:
    result = call_tool(mcp_client, agent.key, "describe_table", {"table": name})
    assert result["status"] == "failed"
    assert result["columns"] == []
    assert result["message"] == agent_messages.table_not_found(name)
    assert action_row(auth_db, result["action_id"])["failure_reason"] == "table_not_found"


# --- query_database ----------------------------------------------------------------------------


def test_count_by_plan_matches_a_direct_reader_query(
    mcp_client: TestClient, agent: McpAgent
) -> None:
    result = query(mcp_client, agent, "SELECT plan, count(*) FROM users GROUP BY plan ORDER BY 1")
    assert result["status"] == "executed"
    assert result["columns"] == ["plan", "count"]
    with psycopg.connect(COMPANY_READER_URL) as conn:
        direct = conn.execute("SELECT plan, count(*) FROM users GROUP BY plan ORDER BY 1")
        assert result["rows"] == [list(row) for row in direct.fetchall()]
    assert result["row_count"] == 3
    assert result["truncated"] is False
    assert result["message"] is None


def test_more_than_500_rows_are_cut_with_the_notice(
    mcp_client: TestClient, agent: McpAgent
) -> None:
    for sql in ("SELECT id FROM users ORDER BY id LIMIT 501", "SELECT id FROM users ORDER BY id"):
        result = query(mcp_client, agent, sql)
        assert result["row_count"] == 500
        assert len(result["rows"]) == 500
        assert result["rows"][-1] == [500]
        assert result["truncated"] is True
        assert result["message"] == agent_messages.TRUNCATED


def test_exactly_500_rows_have_no_notice(mcp_client: TestClient, agent: McpAgent) -> None:
    result = query(mcp_client, agent, "SELECT id FROM users ORDER BY id LIMIT 500")
    assert result["row_count"] == 500
    assert result["truncated"] is False
    assert result["message"] is None


def test_slow_query_hits_the_5_second_statement_timeout(
    mcp_client: TestClient, agent: McpAgent, auth_db: AuthDb
) -> None:
    started = time.perf_counter()
    result = query(mcp_client, agent, "SELECT pg_sleep(6)")
    elapsed = time.perf_counter() - started
    assert 4.5 < elapsed < 6.0, elapsed
    assert result["status"] == "failed"
    assert result["message"] == agent_messages.failed("statement_timeout")
    assert action_row(auth_db, result["action_id"])["failure_reason"] == "statement_timeout"


@pytest.mark.parametrize(
    ("sql", "sqlstate", "text"),
    [
        ("SELECT * FROM userz", "42P01", 'relation "userz" does not exist'),
        ("SELECT 'abc'::int", "22P02", 'invalid input syntax for type integer: "abc"'),
        ("SELECT * FROM blastproof.applied_actions", "42501", "permission denied for schema"),
    ],
)
def test_query_errors_tell_the_agent_but_store_only_the_code(
    mcp_client: TestClient,
    agent: McpAgent,
    auth_db: AuthDb,
    capsys: pytest.CaptureFixture[str],
    sql: str,
    sqlstate: str,
    text: str,
) -> None:
    configure_logging()  # onto this test's captured stdout
    result = query(mcp_client, agent, sql)
    assert result["status"] == "failed"
    assert result["message"].startswith("The query failed: ")
    assert text in result["message"]
    assert f"(SQLSTATE {sqlstate})" in result["message"]
    row = action_row(auth_db, result["action_id"])
    assert row["failure_reason"] == f"query_error_{sqlstate}"
    # The Postgres text is never stored or logged (D-104); the raw SQL is stored, never logged.
    stored = auth_db.execute(
        "SELECT data::text FROM audit_events WHERE action_id = %s", (result["action_id"],)
    )
    assert text not in str(stored)
    output = capsys.readouterr().out
    assert f'"reason_code": "query_error_{sqlstate}"' in output
    assert text not in output
    assert sql not in output


def test_a_write_is_refused_and_nothing_changes(
    mcp_client: TestClient, agent: McpAgent, auth_db: AuthDb
) -> None:
    before = company_count("SELECT count(*) FROM customer_notes")
    for sql in (
        "DELETE FROM customer_notes",
        "WITH gone AS (DELETE FROM customer_notes RETURNING id) SELECT count(*) FROM gone",
        "SELECT 1; DELETE FROM customer_notes",
    ):
        result = query(mcp_client, agent, sql, intent="clean up notes")
        assert result["status"] == "failed"
        assert result["message"] == agent_messages.not_supported()
        row = action_row(auth_db, result["action_id"])
        assert row["failure_reason"] == "not_supported"
        assert row["statement_type"] is None
    assert company_count("SELECT count(*) FROM customer_notes") == before


async def test_the_database_refuses_a_write_even_without_the_check() -> None:
    """Layers behind check_read: run_read's READ ONLY transaction, then the reader's privileges."""
    conn = await asyncpg.connect(COMPANY_READER_URL, server_settings=SERVER_SETTINGS)
    try:
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await reads.run_read(conn, "INSERT INTO customer_notes (user_id, body) VALUES (1, 'x')")
        # nextval is stopped twice over: no sequence privilege, and a read-only transaction.
        with pytest.raises(
            (asyncpg.ReadOnlySQLTransactionError, asyncpg.InsufficientPrivilegeError)
        ):
            await reads.run_read(conn, "SELECT nextval('customer_notes_id_seq')")
        # Outside a read-only transaction the role itself has no write privilege.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO customer_notes (user_id, body) VALUES (1, 'x')")
    finally:
        await conn.close()


def test_values_are_made_json_safe(mcp_client: TestClient, agent: McpAgent) -> None:
    result = query(
        mcp_client,
        agent,
        "SELECT timestamptz '2026-10-01 12:00:00+00' AS t, date '2026-10-01' AS d,"
        " 1.50::numeric AS n, '6f0c1b9e-3c3c-4a35-9a54-5a8a1d3b1e10'::uuid AS u,"
        " '\\x00ff'::bytea AS b, interval '1 day' AS i, 'NaN'::float8 AS f, 0.1::float8 AS g,"
        " ARRAY[1, 2] AS a, '{\"k\": 1}'::jsonb AS j, true AS ok, NULL AS nothing",
    )
    assert result["status"] == "executed", result
    assert result["rows"] == [
        [
            "2026-10-01T12:00:00+00:00",
            "2026-10-01",
            "1.50",
            "6f0c1b9e-3c3c-4a35-9a54-5a8a1d3b1e10",
            "00ff",
            "1 day, 0:00:00",
            "nan",
            0.1,
            [1, 2],
            '{"k": 1}',
            True,
            None,
        ]
    ]


# --- Recording ---------------------------------------------------------------------------------


def test_each_call_is_recorded_under_the_owner(mcp_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent = auth_db.make_agent(owner.id)
    executed = query(mcp_client, agent, "SELECT count(*) FROM users", intent="count users")
    failed = query(mcp_client, agent, "UPDATE users SET plan = 'free'", intent="downgrade")
    listed = call_tool(mcp_client, agent.key, "list_tables")
    described = call_tool(mcp_client, agent.key, "describe_table", {"table": "orders"})

    [(system_id, profile)] = auth_db.execute(
        "SELECT id, connection_profile FROM systems WHERE name = 'company'"
    )
    assert profile == "local"
    for result, tool, status in (
        (executed, "query_database", "executed"),
        (failed, "query_database", "failed"),
        (listed, "list_tables", "executed"),
        (described, "describe_table", "executed"),
    ):
        row = action_row(auth_db, result["action_id"])
        assert row["user_id"] == owner.id
        assert row["agent_id"] == agent.id
        assert row["system_id"] == system_id
        assert row["tool"] == tool
        assert row["status"] == status
        assert row["session_id"] is None  # stateless /mcp (D-097)
        assert row["decision"] is None
        assert row["total_ms"] is not None and row["total_ms"] >= 0
        assert row["pinned_sql"] is None and row["sql_display"] is None  # step 8
        assert history(auth_db, result["action_id"]) == ["received", status]

    row = action_row(auth_db, executed["action_id"])
    assert row["sql"] == "SELECT count(*) FROM users"
    assert row["intent"] == "count users"
    assert row["statement_type"] == "select"
    assert row["tables"] == ["users"]
    assert action_row(auth_db, described["action_id"])["tables"] == ["orders"]
    assert action_row(auth_db, listed["action_id"])["sql"] is None


def test_a_profile_change_fails_closed(auth_db: AuthDb, agent: McpAgent) -> None:
    """Actions for one company database must never be attributed to another's system row."""
    with TestClient(create_app(mcp_settings(auth_db)), base_url=MCP_BASE_URL) as client:
        assert call_tool(client, agent.key, "list_tables")["status"] == "executed"
    neon = mcp_settings(
        auth_db, company_connection_profile="neon", company_db_reader_url=COMPANY_READER_URL
    )
    [(before,)] = auth_db.execute("SELECT count(*) FROM actions")
    with TestClient(create_app(neon), base_url=MCP_BASE_URL) as client:
        result = call_tool(client, agent.key, "list_tables")
    assert result == {
        "action_id": None,
        "status": "failed",
        "tables": [],
        "message": agent_messages.failed("internal_error"),
    }
    [(after,)] = auth_db.execute("SELECT count(*) FROM actions")
    assert after == before


def test_company_db_down_is_recorded_as_failed(auth_db: AuthDb, agent: McpAgent) -> None:
    down = "postgresql://blastproof_reader:x@127.0.0.1:1/company_main"
    settings = mcp_settings(auth_db, local_company_reader_url=down)
    with TestClient(create_app(settings), base_url=MCP_BASE_URL) as client:
        result = query(client, agent, "SELECT 1")
    assert result["status"] == "failed"
    assert result["message"] == agent_messages.failed("company_db_unavailable")
    assert action_row(auth_db, result["action_id"])["failure_reason"] == "company_db_unavailable"


def test_unexpected_errors_never_log_raw_values(
    mcp_client: TestClient,
    agent: McpAgent,
    auth_db: AuthDb,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def boom(*_args: object) -> None:
        raise RuntimeError('invalid input: "alice@example.com" postgres://u:pw-value@h/db')

    monkeypatch.setattr(reads, "run_read", boom)
    configure_logging()  # onto this test's captured stdout
    result = query(mcp_client, agent, "SELECT 1")
    output = capsys.readouterr().out
    assert "alice@example.com" not in output
    assert "pw-value" not in output
    assert '"reason_code": "RuntimeError"' in output
    assert '"event": "tool_failed"' in output
    assert result["message"] == agent_messages.failed("internal_error")
    row = action_row(auth_db, result["action_id"])
    assert (row["status"], row["failure_reason"]) == ("failed", "internal_error")
    assert history(auth_db, result["action_id"]) == ["received", "failed"]


def test_agent_key_and_sql_are_never_logged(
    mcp_client: TestClient, agent: McpAgent, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()  # onto this test's captured stdout
    query(mcp_client, agent, "SELECT count(*) FROM users WHERE email = 'bob@example.com'")
    output = capsys.readouterr().out
    assert agent.key not in output
    assert "bob@example.com" not in output
    assert '"event": "action_finished"' in output


# --- get_action_status -------------------------------------------------------------------------


def status_of(client: TestClient, agent: McpAgent, action_id: str) -> dict:
    return call_tool(client, agent.key, "get_action_status", {"action_id": action_id})


def test_get_action_status_scoped_to_the_owner(mcp_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent, sibling = auth_db.make_agent(owner.id), auth_db.make_agent(owner.id)
    stranger = auth_db.make_agent(auth_db.make_user().id)
    admin = auth_db.make_user(role="admin")
    admins_agent = auth_db.make_agent(admin.id)

    executed = query(mcp_client, agent, "SELECT 1")["action_id"]
    refused = query(mcp_client, agent, "DELETE FROM users")["action_id"]
    missing = call_tool(mcp_client, agent.key, "describe_table", {"table": "userz"})["action_id"]
    bad_query = query(mcp_client, agent, "SELECT * FROM userz")["action_id"]
    [(before,)] = auth_db.execute("SELECT count(*) FROM actions")

    assert status_of(mcp_client, agent, executed) == {
        "action_id": executed,
        "status": "executed",
        "decision": None,
        "message": None,
    }
    # A failed action repeats the message the agent got when it ran.
    assert status_of(mcp_client, agent, refused)["message"] == agent_messages.not_supported()
    assert status_of(mcp_client, agent, missing)["message"] == agent_messages.table_not_found(
        "userz"
    )
    # The Postgres text was never stored (D-104), so a query error comes back as its code.
    assert status_of(mcp_client, agent, bad_query)["message"] == agent_messages.failed(
        "query_error_42P01"
    )
    # Agents of the same owner see each other's actions (D-055).
    assert status_of(mcp_client, sibling, executed)["status"] == "executed"
    # Another owner's agent, even an admin's, gets not_found: existence is never revealed.
    for other in (stranger, admins_agent):
        assert status_of(mcp_client, other, executed) == {
            "action_id": executed,
            "status": None,
            "decision": None,
            "message": agent_messages.not_found(executed),
        }
    for unknown in ("6f0c1b9e-3c3c-4a35-9a54-5a8a1d3b1e10", "not-an-id", ""):
        assert status_of(mcp_client, agent, unknown)["message"] == agent_messages.not_found(unknown)
    # Asking creates no action.
    [(after,)] = auth_db.execute("SELECT count(*) FROM actions")
    assert after == before


def test_wait_seconds_is_validated(mcp_client: TestClient, agent: McpAgent) -> None:
    executed = query(mcp_client, agent, "SELECT 1")["action_id"]
    ok = call_tool(
        mcp_client, agent.key, "get_action_status", {"action_id": executed, "wait_seconds": 50}
    )
    assert ok["status"] == "executed"
    body, headers = mcp_call("get_action_status", {"action_id": executed, "wait_seconds": 51})
    response = mcp_client.post("/mcp", json=body, headers=headers | bearer(agent.key))
    message = parse_rpc(response.headers["content-type"], response.text)
    assert message.get("error") or message["result"].get("isError")


# --- Done when ---------------------------------------------------------------------------------


def test_done_when_pro_users_count(mcp_client: TestClient, auth_db: AuthDb) -> None:
    """DW-B06, automated: "how many pro users do we have?" through Blastproof, and recorded."""
    owner = auth_db.make_user()
    agent = auth_db.make_agent(owner.id)
    result = query(
        mcp_client,
        agent,
        "SELECT count(*) FROM users WHERE plan = 'pro'",
        intent="How many pro users do we have?",
    )
    assert result["rows"] == [[459]]
    assert company_count("SELECT count(*) FROM users WHERE plan = 'pro'") == 459
    [(user_id, status)] = auth_db.execute(
        "SELECT user_id, status FROM actions WHERE id = %s", (result["action_id"],)
    )
    assert (user_id, status) == (owner.id, "executed")
