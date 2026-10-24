"""SEC-02, SEC-33, DW-B05: get_current_agent accepts only an active agent's key.

Uses a test-only route guarded by get_current_agent (tests/conftest.build_auth_test_app); /mcp
(step 6) and the hook endpoint (step 18) use the same dependency.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_agent_key
from app.logging import configure_logging
from tests.conftest import AuthDb, bearer, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

INVALID_KEY = {
    "error": {"code": "invalid_agent_key", "message": "The agent key is invalid or deactivated."}
}


def assert_401(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/test/agent", headers=headers)
    assert response.status_code == 401
    assert response.json() == INVALID_KEY
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_key_gives_the_agent_and_its_owner(auth_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent = auth_db.make_agent(owner.id)
    response = auth_client.get("/test/agent", headers=bearer(agent.key))
    assert response.status_code == 200
    assert response.json() == {"agent_id": str(agent.id), "owner_id": str(owner.id)}


def test_done_when_wrong_or_missing_key_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    assert_401(auth_client, {})
    assert_401(auth_client, bearer(generate_agent_key().key))  # well-formed, never issued
    assert_401(auth_client, bearer("bp_short"))
    assert_401(auth_client, {"Authorization": "Basic dXNlcjpwYXNz"})


def test_done_when_user_token_is_not_an_agent_key(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    token = login(auth_client, user.email, user.password).json()["access_token"]
    assert_401(auth_client, bearer(token))


def test_done_when_deactivated_agent_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent = auth_db.make_agent(owner.id)
    assert auth_client.get("/test/agent", headers=bearer(agent.key)).status_code == 200
    auth_db.execute("UPDATE agents SET is_active = false WHERE id = %s", (agent.id,))
    assert_401(auth_client, bearer(agent.key))


def test_agent_of_a_deactivated_owner_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent = auth_db.make_agent(owner.id)
    auth_db.execute("UPDATE users SET is_active = false WHERE id = %s", (owner.id,))
    assert_401(auth_client, bearer(agent.key))


def test_logs_never_hold_the_key(
    auth_client: TestClient, auth_db: AuthDb, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(stream=None)
    agent = auth_db.make_agent(auth_db.make_user().id)
    wrong = generate_agent_key().key
    auth_client.get("/test/agent", headers=bearer(agent.key))
    auth_client.get("/test/agent", headers=bearer(wrong))
    output = capsys.readouterr().out
    assert '"reason": "unknown_key"' in output
    assert agent.key not in output
    assert wrong not in output
