"""SEC-07, DW-B05: an admin-only route (test-only, guarded by require_admin) refuses members."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AuthDb, bearer, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


def token(client: TestClient, auth_db: AuthDb, role: str) -> str:
    user = auth_db.make_user(role=role)
    return login(client, user.email, user.password).json()["access_token"]


def test_done_when_member_gets_403(auth_client: TestClient, auth_db: AuthDb) -> None:
    response = auth_client.get(
        "/test/admin-only", headers=bearer(token(auth_client, auth_db, "member"))
    )
    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "forbidden", "message": "Only admins can do this."}
    }


def test_admin_gets_through(auth_client: TestClient, auth_db: AuthDb) -> None:
    response = auth_client.get(
        "/test/admin-only", headers=bearer(token(auth_client, auth_db, "admin"))
    )
    assert response.status_code == 200


def test_no_token_is_401_not_403(auth_client: TestClient) -> None:
    assert auth_client.get("/test/admin-only").status_code == 401


def test_demoted_admin_is_refused_at_once(auth_client: TestClient, auth_db: AuthDb) -> None:
    """The role is read from the database on every request, not from the token."""
    user = auth_db.make_user(role="admin")
    admin_token = login(auth_client, user.email, user.password).json()["access_token"]
    auth_db.execute("UPDATE users SET role = 'member' WHERE id = %s", (user.id,))
    assert auth_client.get("/test/admin-only", headers=bearer(admin_token)).status_code == 403
