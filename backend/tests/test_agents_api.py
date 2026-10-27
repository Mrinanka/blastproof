"""BE-113: agents list, create, deactivate. Needs Docker."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_agent_key
from tests.action_helpers import token_for
from tests.admin_api_helpers import at_debug, build_admin_app, client_for, logged_text
from tests.conftest import AuthDb

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


@pytest.fixture
def client(auth_db: AuthDb) -> Iterator[TestClient]:
    yield from client_for(build_admin_app(auth_db, public_base_url="http://localhost:8000"))


def create(client, user, name="Claude on my laptop"):
    return client.post("/v1/agents", headers=token_for(user), json={"name": name})


def test_create_shows_the_key_once_and_it_works(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    response = create(client, owner, "  My agent  ")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    key = body["key"]
    assert body["name"] == "My agent"
    assert body["owner"]["id"] == str(owner.id)
    assert body["mcp_url"] == "http://localhost:8000/mcp"
    assert body["claude_mcp_add"].endswith(f'--header "Authorization: Bearer {key}"')
    assert body["agent_key_env"] == f"BLASTPROOF_AGENT_KEY={key}"
    assert key.startswith(body["key_prefix"])
    # The key authenticates the agent on a route that takes agent keys (the hook).
    hook = client.post(
        "/v1/hook/pre-tool-use",
        headers={"Authorization": f"Bearer {key}"},
        json={"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/w"},
    )
    assert hook.status_code == 200
    # Only the hash is stored.
    [(stored,)] = auth_db.execute("SELECT api_key_hash FROM agents WHERE id = %s", (body["id"],))
    assert stored == hash_agent_key(key)


def test_lists_never_hold_the_key_or_its_hash(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    created = create(client, owner).json()
    listing = client.get("/v1/agents", headers=token_for(owner))
    assert listing.status_code == 200
    assert created["key"] not in listing.text
    assert hash_agent_key(created["key"]) not in listing.text
    assert "api_key_hash" not in listing.text
    [item] = listing.json()["items"]
    assert set(item) == {"id", "name", "key_prefix", "is_active", "created_at", "owner"}
    assert set(item["owner"]) == {"id", "display_name"}  # no email


def test_members_see_their_own_agents_and_admins_see_all(
    client: TestClient, auth_db: AuthDb
) -> None:
    owner, other, admin = auth_db.make_user(), auth_db.make_user(), auth_db.make_user(role="admin")
    mine = create(client, owner, "first").json()
    newer = create(client, owner, "second").json()
    theirs = create(client, other).json()
    ids = [i["id"] for i in client.get("/v1/agents", headers=token_for(owner)).json()["items"]]
    assert ids == [newer["id"], mine["id"]]  # newest first, nobody else's
    everyone = client.get("/v1/agents?limit=100", headers=token_for(admin)).json()
    assert {mine["id"], newer["id"], theirs["id"]} <= {i["id"] for i in everyone["items"]}
    assert everyone["total"] >= 3


def test_paging(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    made = [create(client, owner, f"agent {i}").json()["id"] for i in range(3)]
    page = client.get("/v1/agents?limit=1&offset=1", headers=token_for(owner)).json()
    assert [i["id"] for i in page["items"]] == [made[1]]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)


def test_deactivate_by_owner_and_by_admin(client: TestClient, auth_db: AuthDb) -> None:
    owner, admin = auth_db.make_user(), auth_db.make_user(role="admin")
    first, second = create(client, owner).json(), create(client, owner).json()
    hook = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/w"}

    def key_works(created: dict) -> bool:
        headers = {"Authorization": f"Bearer {created['key']}"}
        return client.post("/v1/hook/pre-tool-use", headers=headers, json=hook).status_code == 200

    assert key_works(first) and key_works(second)
    done = client.post(f"/v1/agents/{first['id']}/deactivate", headers=token_for(owner))
    assert done.status_code == 200 and done.json()["is_active"] is False
    assert "key" not in done.json()
    again = client.post(f"/v1/agents/{first['id']}/deactivate", headers=token_for(owner))
    assert again.status_code == 200  # twice is fine
    assert (
        client.post(f"/v1/agents/{second['id']}/deactivate", headers=token_for(admin)).status_code
        == 200
    )
    assert not key_works(first) and not key_works(second)  # the keys now get 401


def test_another_members_agent_is_404_and_stays_active(client: TestClient, auth_db: AuthDb) -> None:
    owner, other = auth_db.make_user(), auth_db.make_user()
    created = create(client, owner).json()
    for agent_id in (created["id"], str(uuid.uuid4()), "not-a-uuid"):
        response = client.post(f"/v1/agents/{agent_id}/deactivate", headers=token_for(other))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    [(active,)] = auth_db.execute("SELECT is_active FROM agents WHERE id = %s", (created["id"],))
    assert active is True


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_bad_names_are_422(client: TestClient, auth_db: AuthDb, name: str) -> None:
    response = create(client, auth_db.make_user(), name)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_extra_fields_are_refused(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    other = auth_db.make_user()
    response = client.post(
        "/v1/agents", headers=token_for(owner), json={"name": "a", "owner_id": str(other.id)}
    )
    assert response.status_code == 422  # an agent is always made for the signed-in user


def test_the_key_is_not_logged(
    client: TestClient, auth_db: AuthDb, caplog: pytest.LogCaptureFixture
) -> None:
    at_debug(caplog)
    owner = auth_db.make_user()
    key = create(client, owner).json()["key"]
    client.get("/v1/agents", headers=token_for(owner))
    assert key not in logged_text(caplog)


def test_anonymous_gets_401(client: TestClient) -> None:
    assert client.get("/v1/agents").status_code == 401
    assert client.post("/v1/agents", json={"name": "a"}).status_code == 401
