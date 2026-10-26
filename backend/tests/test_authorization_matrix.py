"""TEST-03 (started): every `/v1` route as anonymous, the owner, another member and an admin.

Step 19 adds a row for each route it builds. The guard at the bottom fails when a route has no row.
"""

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.action_helpers import insert_action, make_system, token_for
from tests.conftest import AuthDb, TestUser

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


@pytest.mark.parametrize(("method", "template"), list(MATRIX))
@pytest.mark.parametrize("role", ROLES)
def test_every_route_for_every_role(
    auth_client: TestClient, actors: Actors, method: str, template: str, role: str
) -> None:
    path = template.replace("{action_id}", str(actors.action))
    response = auth_client.request(method, path, headers=actors.headers(role))
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
