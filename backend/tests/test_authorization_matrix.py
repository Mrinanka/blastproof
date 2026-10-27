"""TEST-03 (started): every `/v1` route as anonymous, the owner, another member and an admin.

Step 19 added a row for each route it built. The guard at the bottom fails when a route has no row.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.clock import SystemClock
from app.main import create_app
from app.workflow.runner import WorkflowRunner
from tests.action_helpers import insert_action, make_system, token_for
from tests.admin_api_helpers import build_admin_app
from tests.conftest import AuthDb, TestUser, mcp_settings, with_client_ip

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

ROLES = ("anonymous", "owner", "other", "admin")

# (method, route template) -> the status each role gets. `{action}` is the owner's action.
MATRIX: dict[tuple[str, str], dict[str, int]] = {
    ("POST", "/v1/auth/login"): dict.fromkeys(ROLES, 422),  # public; an empty body is invalid
    ("GET", "/v1/auth/me"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("GET", "/v1/actions"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("GET", "/v1/actions/{action_id}"): {
        "anonymous": 401,
        "owner": 200,
        "other": 404,
        "admin": 200,
    },
    # The matrix's action is an `executed` read, so the claim always loses and no graph is touched
    # (200 is covered on the real graph in test_decisions_api.py).
    ("POST", "/v1/actions/{action_id}/approve"): {
        "anonymous": 401,
        "owner": 409,
        "other": 404,
        "admin": 409,
    },
    ("POST", "/v1/actions/{action_id}/reject"): {
        "anonymous": 401,
        "owner": 409,
        "other": 404,
        "admin": 409,
    },
    # An agent key is the only credential the hook accepts, so a dashboard user's JWT (any role)
    # and no credential at all are both 401, whatever the body (a dependency runs first).
    ("POST", "/v1/hook/pre-tool-use"): dict.fromkeys(ROLES, 401),
    # Step 19. Anyone signed in reads; only admins change knowledge, scan systems and manage users.
    ("GET", "/v1/stats"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("GET", "/v1/policies"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("GET", "/v1/knowledge"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    # The admin's upload has no file, so it stops at 422 before anything is embedded (the real
    # upload is in test_knowledge_api.py).
    ("POST", "/v1/knowledge"): {"anonymous": 401, "owner": 403, "other": 403, "admin": 422},
    ("DELETE", "/v1/knowledge/{source}"): {
        "anonymous": 401,
        "owner": 403,
        "other": 403,
        "admin": 200,
    },
    ("GET", "/v1/systems"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("POST", "/v1/systems/{system_id}/scan"): {
        "anonymous": 401,
        "owner": 403,
        "other": 403,
        "admin": 200,
    },
    ("GET", "/v1/agents"): {"anonymous": 401, "owner": 200, "other": 200, "admin": 200},
    ("POST", "/v1/agents"): {"anonymous": 401, "owner": 201, "other": 201, "admin": 201},
    # Rows run owner, other, admin: the owner's agent is turned off, another member cannot see it,
    # and the admin turning it off again is fine (deactivating twice is 200).
    ("POST", "/v1/agents/{agent_id}/deactivate"): {
        "anonymous": 401,
        "owner": 200,
        "other": 404,
        "admin": 200,
    },
    ("GET", "/v1/users"): {"anonymous": 401, "owner": 403, "other": 403, "admin": 200},
    ("POST", "/v1/users"): {"anonymous": 401, "owner": 403, "other": 403, "admin": 201},
}

# A request body for the rows that need a valid one to reach their authorization check. A callable
# makes a new body for each request (a new user needs an email nobody has used).
BODIES: dict[tuple[str, str], dict[str, str] | Callable[[], dict[str, str]]] = {
    ("POST", "/v1/actions/{action_id}/reject"): {"reason": "Not this table, ask first."},
    ("POST", "/v1/agents"): {"name": "matrix agent"},
    ("POST", "/v1/users"): lambda: {
        "email": f"matrix-{uuid.uuid4().hex[:10]}@example.com",
        "display_name": "Matrix User",
        "password": "a long enough password",
    },
}

SOURCE = "matrix-notes.md"


@dataclass
class Actors:
    owner: TestUser
    other: TestUser
    admin: TestUser
    action: uuid.UUID
    agent: uuid.UUID  # the owner's
    system: uuid.UUID
    source: str  # a knowledge source that exists

    def headers(self, role: str) -> dict[str, str]:
        return {} if role == "anonymous" else token_for(getattr(self, role))


@pytest.fixture
def actors(auth_db: AuthDb) -> Actors:
    owner, other, admin = auth_db.make_user(), auth_db.make_user(), auth_db.make_user(role="admin")
    agent = auth_db.make_agent(owner.id)
    system = make_system(auth_db)
    action = insert_action(auth_db, owner, agent.id, system)
    auth_db.execute("DELETE FROM knowledge_chunks WHERE source = %s", (SOURCE,))
    auth_db.execute(
        "INSERT INTO knowledge_chunks (source, table_names, content) VALUES (%s, '{}', 'x')",
        (SOURCE,),
    )
    return Actors(owner, other, admin, action, agent.id, system, SOURCE)


@pytest.fixture
def matrix_client(auth_db: AuthDb) -> Iterator[TestClient]:
    """The test app with a recovered runner, which the decision routes need. Its graph is never
    used: every matrix action is already settled, so no decision is claimed. It also has a
    knowledge service that needs no model and the local company reader, so an admin's delete and
    scan really succeed."""
    settings = mcp_settings(auth_db)
    app = build_admin_app(auth_db)
    with TestClient(with_client_ip(app)) as client:
        runner = WorkflowRunner(
            None,  # type: ignore[arg-type]
            app.state.sessionmaker,
            SystemClock(),
            settings,
        )
        runner.mark_recovered()
        app.state.runner = runner
        yield client


@pytest.mark.parametrize(("method", "template"), list(MATRIX))
@pytest.mark.parametrize("role", ROLES)
def test_every_route_for_every_role(
    matrix_client: TestClient, actors: Actors, method: str, template: str, role: str
) -> None:
    path = (
        template.replace("{action_id}", str(actors.action))
        .replace("{agent_id}", str(actors.agent))
        .replace("{system_id}", str(actors.system))
        .replace("{source}", actors.source)
    )
    body = BODIES.get((method, template))
    response = matrix_client.request(
        method, path, headers=actors.headers(role), json=body() if callable(body) else body
    )
    assert response.status_code == MATRIX[(method, template)][role], (method, path, role)


def test_every_v1_route_has_a_row_in_the_matrix(auth_settings: Settings) -> None:
    # From the OpenAPI schema: included routers are nested in this FastAPI, not in app.routes.
    paths = create_app(auth_settings).openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/v1/")
        for method in operations
    }
    assert routes == set(MATRIX), f"routes without a matrix row: {sorted(routes - set(MATRIX))}"


def test_hook_endpoint_agent_keys(
    matrix_client: TestClient, actors: Actors, auth_db: AuthDb
) -> None:
    """The one credential the hook route takes is an agent key (a JWT is refused above)."""
    url = "/v1/hook/pre-tool-use"
    body = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/w"}
    agent = auth_db.make_agent(actors.owner.id)
    ok = matrix_client.post(url, json=body, headers={"Authorization": f"Bearer {agent.key}"})
    assert ok.status_code == 200 and ok.json()["decision"] == "allow"
    wrong = matrix_client.post(url, json=body, headers={"Authorization": "Bearer bp_" + "A" * 43})
    assert wrong.status_code == 401
    auth_db.execute("UPDATE agents SET is_active = false WHERE id = %s", (agent.id,))
    off = matrix_client.post(url, json=body, headers={"Authorization": f"Bearer {agent.key}"})
    assert off.status_code == 401
