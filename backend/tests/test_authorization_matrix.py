"""TEST-03 (started): every `/v1` route as anonymous, the owner, another member and an admin.

Step 19 adds a row for each route it builds. The guard at the bottom fails when a route has no row.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.clock import SystemClock
from app.main import create_app
from app.workflow.runner import WorkflowRunner
from tests.action_helpers import insert_action, make_system, token_for
from tests.conftest import AuthDb, TestUser, build_auth_test_app, with_client_ip

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
}

# A request body for the rows that need a valid one to reach their authorization check.
BODIES: dict[tuple[str, str], dict[str, str]] = {
    ("POST", "/v1/actions/{action_id}/reject"): {"reason": "Not this table, ask first."},
}


@dataclass
class Actors:
    owner: TestUser
    other: TestUser
    admin: TestUser
    action: uuid.UUID

    def headers(self, role: str) -> dict[str, str]:
        return {} if role == "anonymous" else token_for(getattr(self, role))


@pytest.fixture
def actors(auth_db: AuthDb) -> Actors:
    owner, other, admin = auth_db.make_user(), auth_db.make_user(), auth_db.make_user(role="admin")
    agent = auth_db.make_agent(owner.id)
    action = insert_action(auth_db, owner, agent.id, make_system(auth_db))
    return Actors(owner, other, admin, action)


@pytest.fixture
def matrix_client(auth_settings: Settings) -> Iterator[TestClient]:
    """The test app with a recovered runner, which the decision routes need. Its graph is never
    used: every matrix action is already settled, so no decision is claimed."""
    app = build_auth_test_app(auth_settings)
    with TestClient(with_client_ip(app)) as client:
        runner = WorkflowRunner(
            None,  # type: ignore[arg-type]
            app.state.sessionmaker,
            SystemClock(),
            auth_settings,
        )
        runner.mark_recovered()
        app.state.runner = runner
        yield client


@pytest.mark.parametrize(("method", "template"), list(MATRIX))
@pytest.mark.parametrize("role", ROLES)
def test_every_route_for_every_role(
    matrix_client: TestClient, actors: Actors, method: str, template: str, role: str
) -> None:
    path = template.replace("{action_id}", str(actors.action))
    response = matrix_client.request(
        method, path, headers=actors.headers(role), json=BODIES.get((method, template))
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
