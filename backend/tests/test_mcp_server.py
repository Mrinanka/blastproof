"""Step 6: how `/mcp` is mounted, protected and authenticated (D-096 to D-099, SEC-33, BE-17).

Requests are raw 2026-07-28 JSON-RPC, the session-less protocol Claude Code speaks (D-108).
Which agent a tool saw is read back from the `actions` row it recorded.
"""

import asyncio
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.security import generate_agent_key
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.mcp.auth import NoAgentError, current_agent, require_agent
from app.mcp.server import build_mcp_server, transport_security
from tests.conftest import (
    MCP_BASE_URL,
    AuthDb,
    asyncpg_url,
    bearer,
    call_tool,
    login,
    mcp_call,
    mcp_settings,
    parse_rpc,
)

INVALID_KEY = {
    "error": {"code": "invalid_agent_key", "message": "The agent key is invalid or deactivated."}
}
UNAVAILABLE = {"error": {"code": "unavailable", "message": "Blastproof's database is unavailable."}}


def post_list_tables(client: TestClient, headers: dict[str, str]):  # -> TestClient response
    body, mcp_headers = mcp_call("list_tables")
    return client.post("/mcp", json=body, headers=mcp_headers | headers)


def recorded_by(auth_db: AuthDb, action_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    [(agent_id, user_id)] = auth_db.execute(
        "SELECT agent_id, user_id FROM actions WHERE id = %s", (action_id,)
    )
    return agent_id, user_id


# --- Mounting (unit) ---------------------------------------------------------------------------


def test_sdk_app_has_exactly_one_route(settings: Settings) -> None:
    # D-096 takes the SDK app's routes; if the SDK ever adds routes or middleware, look again.
    mcp = build_mcp_server(settings, None)
    sdk_app = mcp.streamable_http_app(
        stateless_http=True, json_response=False, transport_security=transport_security(settings)
    )
    assert [route.path for route in sdk_app.routes] == ["/mcp"]
    assert sdk_app.user_middleware == []


def test_mcp_is_not_in_openapi_and_other_paths_still_work(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url=MCP_BASE_URL) as client:
        assert not any("mcp" in path for path in client.get("/openapi.json").json()["paths"])
        assert client.get("/health").json() == {"status": "ok"}
        missing = client.get("/nope")
        # FastAPI's own 404, not the SDK's: the route joined FastAPI's router (D-096).
        assert missing.status_code == 404
        assert missing.json() == {"error": {"code": "not_found", "message": "No such route."}}
        # Only /mcp exactly is guarded; /mcpx is just an unknown path.
        assert client.post("/mcpx").status_code == 404


def test_transport_security_allows_loopback_and_the_public_host() -> None:
    security = transport_security(
        Settings(_env_file=None, public_base_url="https://blastproof.example.com")
    )
    assert security.enable_dns_rebinding_protection
    assert security.allowed_hosts == ["localhost:*", "127.0.0.1:*", "blastproof.example.com"]
    assert security.allowed_origins == [
        "http://localhost:*",
        "http://127.0.0.1:*",
        "https://blastproof.example.com",
    ]
    # The default PUBLIC_BASE_URL is already covered by the loopback patterns.
    default = transport_security(Settings(_env_file=None))
    assert "localhost:8000" in default.allowed_hosts


def test_without_a_database_mcp_answers_503(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url=MCP_BASE_URL) as client:
        response = post_list_tables(client, bearer(generate_agent_key().key))
        assert response.status_code == 503
        assert response.json() == UNAVAILABLE


def test_require_agent_without_an_agent_raises() -> None:
    assert current_agent.get() is None
    with pytest.raises(NoAgentError):
        require_agent()


# --- With a database ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
def test_post_mcp_with_a_key_is_200(mcp_client: TestClient, auth_db: AuthDb) -> None:
    agent = auth_db.make_agent(auth_db.make_user().id)
    response = post_list_tables(mcp_client, bearer(agent.key))
    assert response.status_code == 200
    message = parse_rpc(response.headers["content-type"], response.text)
    assert message["result"]["structuredContent"]["status"] == "executed"


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
def test_two_apps_built_one_after_another_both_work(auth_db: AuthDb) -> None:
    # session_manager.run() works once per MCP server, so each app needs its own (D-096).
    agent = auth_db.make_agent(auth_db.make_user().id)
    for _ in range(2):
        app = create_app(mcp_settings(auth_db))
        with TestClient(app, base_url=MCP_BASE_URL) as client:
            assert call_tool(client, agent.key, "list_tables")["status"] == "executed"


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
def test_unlisted_host_gets_421_and_foreign_origin_403(auth_db: AuthDb) -> None:
    agent = auth_db.make_agent(auth_db.make_user().id)
    key = bearer(agent.key)
    app = create_app(mcp_settings(auth_db, public_base_url="https://blastproof.example.com"))
    with TestClient(app) as client:  # TestClient's default host is "testserver"
        assert post_list_tables(client, key).status_code == 421
        public = {"host": "blastproof.example.com"}
        assert post_list_tables(client, key | public).status_code == 200
        local = {"host": "localhost:8000"}
        assert post_list_tables(client, key | local).status_code == 200
        foreign = {"origin": "https://evil.example"}
        assert post_list_tables(client, key | local | foreign).status_code == 403
        allowed_origin = {"origin": "https://blastproof.example.com"}
        assert post_list_tables(client, key | public | allowed_origin).status_code == 200


# --- Auth ----------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
def test_bad_keys_get_401_with_the_envelope(mcp_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    inactive_agent = auth_db.make_agent(owner.id, is_active=False)
    inactive_owner = auth_db.make_user(is_active=False)
    agent_of_inactive_owner = auth_db.make_agent(inactive_owner.id)
    user_token = login(mcp_client, owner.email, owner.password).json()["access_token"]
    cases = {
        "missing": {},
        "malformed": bearer("bp_short"),
        "unknown": bearer(generate_agent_key().key),
        "deactivated agent": bearer(inactive_agent.key),
        "deactivated owner": bearer(agent_of_inactive_owner.key),
        "basic scheme": {"Authorization": "Basic dXNlcjpwYXNz"},
        "user token": bearer(user_token),
    }
    for case, headers in cases.items():
        response = post_list_tables(mcp_client, headers)
        assert response.status_code == 401, case
        assert response.json() == INVALID_KEY, case
        assert response.headers["www-authenticate"] == "Bearer", case
    # Nothing was recorded for any of them.
    [(count,)] = auth_db.execute("SELECT count(*) FROM actions WHERE user_id = %s", (owner.id,))
    assert count == 0


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
def test_unreachable_database_gets_503(auth_db: AuthDb) -> None:
    # Port 1 on loopback refuses the connection: the key cannot be checked, so the call fails.
    down = "postgresql+asyncpg://blastproof_app:x@127.0.0.1:1/blastproof"
    app = create_app(mcp_settings(auth_db, blastproof_database_url=down))
    with TestClient(app, base_url=MCP_BASE_URL) as client:
        response = post_list_tables(client, bearer(generate_agent_key().key))
        assert response.status_code == 503
        assert response.json() == UNAVAILABLE


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
def test_interleaved_agents_and_a_key_change_are_each_recorded_right(
    mcp_client: TestClient, auth_db: AuthDb
) -> None:
    owner_a, owner_b = auth_db.make_user(), auth_db.make_user()
    agent_a, agent_b = auth_db.make_agent(owner_a.id), auth_db.make_agent(owner_b.id)
    expected = {agent_a.key: (agent_a.id, owner_a.id), agent_b.key: (agent_b.id, owner_b.id)}
    # The same client switches keys on every call.
    for key in (agent_a.key, agent_b.key, agent_b.key, agent_a.key, agent_b.key):
        action_id = call_tool(mcp_client, key, "list_tables")["action_id"]
        assert recorded_by(auth_db, action_id) == expected[key]


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
async def test_concurrent_agents_are_each_recorded_right(auth_db: AuthDb) -> None:
    """Overlapping calls on one event loop (as uvicorn runs them) never swap agents (D-098)."""
    owners = [auth_db.make_user() for _ in range(3)]
    agents = [auth_db.make_agent(owner.id) for owner in owners]
    app = create_app(mcp_settings(auth_db))

    async def call(client: httpx.AsyncClient, key: str) -> str:
        body, headers = mcp_call(
            "query_database", {"sql": "SELECT pg_sleep(0.3), 1", "intent": "overlap test"}
        )
        response = await client.post("/mcp", json=body, headers=headers | bearer(key))
        assert response.status_code == 200
        message = parse_rpc(response.headers["content-type"], response.text)
        return message["result"]["structuredContent"]["action_id"]

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=MCP_BASE_URL) as client:
            keys = [agent.key for agent in agents] * 3
            started = time.perf_counter()
            action_ids = await asyncio.gather(*(call(client, key) for key in keys))
            elapsed = time.perf_counter() - started

    # Nine 0.3 s calls one after another would take 2.7 s; they really ran at the same time.
    assert elapsed < 2.0, elapsed

    by_key = {agent.key: (agent.id, owner.id) for agent, owner in zip(agents, owners, strict=True)}
    for key, action_id in zip(keys, action_ids, strict=True):
        assert recorded_by(auth_db, action_id) == by_key[key]


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
async def test_a_tool_without_an_agent_fails_and_records_nothing(auth_db: AuthDb) -> None:
    """If a handler ever ran outside the middleware's context, it must refuse (D-098)."""
    settings = mcp_settings(auth_db)
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        mcp = build_mcp_server(settings, make_sessionmaker(engine))
        [(before,)] = auth_db.execute("SELECT count(*) FROM actions")
        result = await mcp.call_tool("query_database", {"sql": "SELECT 1", "intent": "x"})
        [(after,)] = auth_db.execute("SELECT count(*) FROM actions")
    finally:
        await engine.dispose()
    assert result.structured_content == {
        "action_id": None,
        "decision": None,
        "status": "failed",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "message": "Blastproof could not complete this safely, so nothing was changed."
        " Reason: not_authenticated.",
    }
    assert after == before


def test_wait_seconds_limit_comes_from_config(settings: Settings) -> None:
    async def schema(max_wait: int) -> dict:
        mcp = build_mcp_server(
            settings.model_copy(update={"status_max_wait_seconds": max_wait}), None
        )
        [tool] = [t for t in await mcp.list_tools() if t.name == "get_action_status"]
        return tool.input_schema["properties"]["wait_seconds"]

    assert asyncio.run(schema(50))["maximum"] == 50
    assert asyncio.run(schema(20))["maximum"] == 20
    assert "0 to 20 seconds" in asyncio.run(schema(20))["description"]
