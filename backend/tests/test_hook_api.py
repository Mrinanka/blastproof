"""Step 18: POST /v1/hook/pre-tool-use (BE-101, BE-102, BE-105, BE-106, SEC-33)."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.security import generate_agent_key
from app.logging import configure_logging
from app.main import create_app
from tests.conftest import MCP_BASE_URL, AuthDb, TestAgent, TestUser, asyncpg_url, bearer, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

URL = "/v1/hook/pre-tool-use"
CWD = r"D:\My Workplace\blastproof-demo"
INVALID_KEY = {
    "error": {"code": "invalid_agent_key", "message": "The agent key is invalid or deactivated."}
}


def call(tool: str = "Bash", cwd: str = CWD, **tool_input: object) -> dict[str, object]:
    return {"tool_name": tool, "tool_input": tool_input, "cwd": cwd, "project_dir": cwd}


def events(db: AuthDb, agent: TestAgent) -> list[tuple]:
    return db.execute(
        "SELECT user_id, agent_id, tool, summary, decision, reason, rule FROM hook_events"
        " WHERE agent_id = %s ORDER BY created_at",
        (agent.id,),
    )


@pytest.fixture
def owner(auth_db: AuthDb) -> TestUser:
    return auth_db.make_user()


@pytest.fixture
def agent(auth_db: AuthDb, owner: TestUser) -> TestAgent:
    return auth_db.make_agent(owner.id)


def test_allow_answers_with_no_reason_and_stores_the_row(
    auth_client: TestClient, auth_db: AuthDb, owner: TestUser, agent: TestAgent
) -> None:
    response = auth_client.post(URL, json=call(command="ls -la"), headers=bearer(agent.key))
    assert response.status_code == 200
    assert response.json() == {"decision": "allow", "reason": None, "rule": None}
    assert events(auth_db, agent) == [
        (owner.id, agent.id, "Bash", "Bash: ls -la", "allow", "No rule matched.", None)
    ]


def test_deny_answers_with_the_rule_and_its_reason(
    auth_client: TestClient, auth_db: AuthDb, owner: TestUser, agent: TestAgent
) -> None:
    response = auth_client.post(
        URL, json=call(command='psql "$DATABASE_URL" -c "select 1"'), headers=bearer(agent.key)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "deny"
    assert body["rule"] == "database_client"
    assert "blastproof MCP tools" in body["reason"]
    [row] = events(auth_db, agent)
    assert row[0] == owner.id and row[1] == agent.id
    assert row[4:] == ("deny", body["reason"], "database_client")


def test_read_of_an_env_file_is_denied(auth_client: TestClient, agent: TestAgent) -> None:
    body = auth_client.post(
        URL, json=call("Read", file_path="../blastproof/backend/.env"), headers=bearer(agent.key)
    ).json()
    assert (body["decision"], body["rule"]) == ("deny", "credential_file")


def test_the_stored_summary_is_masked(
    auth_client: TestClient, auth_db: AuthDb, agent: TestAgent
) -> None:
    command = (
        "curl -u admin:pw https://x.example/a?email=jane.doe@example.com PGPASSWORD=hunter2 ls"
    )
    auth_client.post(URL, json=call(command=command), headers=bearer(agent.key))
    [row] = events(auth_db, agent)
    for leaked in ("jane.doe@example.com", "hunter2"):
        assert leaked not in row[3]


def test_a_hook_call_creates_no_action(
    auth_client: TestClient, auth_db: AuthDb, owner: TestUser, agent: TestAgent
) -> None:
    before = auth_db.execute("SELECT count(*) FROM actions")
    auth_client.post(URL, json=call(command="psql"), headers=bearer(agent.key))
    auth_client.post(URL, json=call(command="ls"), headers=bearer(agent.key))
    assert auth_db.execute("SELECT count(*) FROM actions") == before


def test_each_call_gets_its_own_row(
    auth_client: TestClient, auth_db: AuthDb, agent: TestAgent
) -> None:
    for command in ("ls", "pwd", "psql"):
        auth_client.post(URL, json=call(command=command), headers=bearer(agent.key))
    assert [r[4] for r in events(auth_db, agent)] == ["allow", "allow", "deny"]


# --- authentication ------------------------------------------------------------------------------


def assert_refused(client: TestClient, db: AuthDb, headers: dict[str, str]) -> None:
    before = db.execute("SELECT count(*) FROM hook_events")
    response = client.post(URL, json=call(command="ls"), headers=headers)
    assert response.status_code == 401
    assert response.json() == INVALID_KEY
    assert response.headers["www-authenticate"] == "Bearer"
    assert db.execute("SELECT count(*) FROM hook_events") == before


def test_a_missing_or_wrong_key_is_401_and_writes_no_row(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    assert_refused(auth_client, auth_db, {})
    assert_refused(auth_client, auth_db, bearer(generate_agent_key().key))
    assert_refused(auth_client, auth_db, bearer("bp_short"))


def test_a_user_token_is_not_an_agent_key(
    auth_client: TestClient, auth_db: AuthDb, owner: TestUser
) -> None:
    token = login(auth_client, owner.email, owner.password).json()["access_token"]
    assert_refused(auth_client, auth_db, bearer(token))


def test_a_deactivated_agent_is_401(
    auth_client: TestClient, auth_db: AuthDb, agent: TestAgent
) -> None:
    auth_db.execute("UPDATE agents SET is_active = false WHERE id = %s", (agent.id,))
    assert_refused(auth_client, auth_db, bearer(agent.key))


def test_an_agent_of_a_deactivated_owner_is_401(
    auth_client: TestClient, auth_db: AuthDb, owner: TestUser, agent: TestAgent
) -> None:
    auth_db.execute("UPDATE users SET is_active = false WHERE id = %s", (owner.id,))
    assert_refused(auth_client, auth_db, bearer(agent.key))


# --- the request body ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"tool_name": "", "tool_input": {}},
        {"tool_name": "x" * 65, "tool_input": {}},
        {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": "ls"},
        {"tool_name": "Bash", "tool_input": {}, "cwd": "x" * 4097},
    ],
)
def test_a_bad_body_is_422_in_the_error_envelope(
    auth_client: TestClient, agent: TestAgent, body: dict[str, object]
) -> None:
    response = auth_client.post(URL, json=body, headers=bearer(agent.key))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_body_fields_are_ignored(auth_client: TestClient, agent: TestAgent) -> None:
    body = call(command="ls") | {"hook_event_name": "PreToolUse", "session_id": "abc"}
    assert auth_client.post(URL, json=body, headers=bearer(agent.key)).status_code == 200


# --- logs and schema -----------------------------------------------------------------------------


def test_the_log_holds_neither_the_command_nor_the_path_nor_the_key(
    auth_client: TestClient, agent: TestAgent, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(stream=None)
    command = "psql postgres://u:hunter2-pw@db/x -c 'select alice@example.com'"
    auth_client.post(URL, json=call(command=command), headers=bearer(agent.key))
    auth_client.post(
        URL, json=call("Read", file_path="/secret/path/.env"), headers=bearer(agent.key)
    )
    output = capsys.readouterr().out
    lines = [json.loads(line) for line in output.splitlines() if '"hook_checked"' in line]
    assert [(line["decision"], line["rule"], line["tool"]) for line in lines] == [
        ("deny", "database_client", "Bash"),
        ("deny", "credential_file", "Read"),
    ]
    assert all("duration_ms" in line for line in lines)
    for leaked in ("hunter2", "alice@example.com", "/secret/path", "psql", agent.key):
        assert leaked not in output


def test_the_openapi_schema_shows_the_response_model(auth_settings: Settings) -> None:
    operation = create_app(auth_settings).openapi()["paths"][URL]["post"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/HookDecisionResponse"}
    assert {"401", "422"} <= set(operation["responses"])


def test_the_database_down_is_a_server_error_not_an_allow(auth_db: AuthDb) -> None:
    dead = Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=asyncpg_url("postgresql://blastproof_app:x@127.0.0.1:1/none"),
        jwt_secret="test-only-jwt-secret-" + "x" * 32,
    )
    key = generate_agent_key().key
    with TestClient(create_app(dead), raise_server_exceptions=False, base_url=MCP_BASE_URL) as c:
        response = c.post(URL, json=call(command="ls"), headers=bearer(key))
    # The script treats any 5xx as "unreachable" and fails closed.
    assert response.status_code >= 500
    assert "allow" not in response.text
