"""SEC-05, DW-B05, BE-108: GET /v1/auth/me rejects bad tokens, inactive users and old versions."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import TOKEN_LIFETIME, create_access_token
from tests.conftest import TEST_JWT_SECRET, AuthDb, bearer, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

NOT_AUTHENTICATED = {"error": {"code": "not_authenticated", "message": "Sign in again."}}


def token_for(client: TestClient, auth_db: AuthDb, **user_options: object) -> tuple[str, uuid.UUID]:
    user = auth_db.make_user(**user_options)  # type: ignore[arg-type]
    response = login(client, user.email, user.password)
    assert response.status_code == 200
    return response.json()["access_token"], user.id


def assert_401(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_returns_the_signed_in_user(auth_client: TestClient, auth_db: AuthDb) -> None:
    token, user_id = token_for(auth_client, auth_db)
    response = auth_client.get("/v1/auth/me", headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user_id)
    assert body["role"] == "member"
    assert set(body) == {"id", "email", "display_name", "role"}


def test_done_when_missing_or_wrong_token_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    token, _ = token_for(auth_client, auth_db)
    assert_401(auth_client, {})
    assert_401(auth_client, {"Authorization": "Basic dXNlcjpwYXNz"})
    assert_401(auth_client, bearer("not-a-token"))
    # One character of the signature changed.
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    assert_401(auth_client, bearer(tampered))
    other_secret, _ = create_access_token(uuid.uuid4(), 0, "another-secret-" + "y" * 32)
    assert_401(auth_client, bearer(other_secret))


def test_done_when_expired_token_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    issued = datetime.now(UTC) - TOKEN_LIFETIME - timedelta(seconds=1)
    expired, _ = create_access_token(user.id, 0, TEST_JWT_SECRET, now=issued)
    assert_401(auth_client, bearer(expired))


def test_token_for_a_user_that_does_not_exist_is_401(auth_client: TestClient) -> None:
    token, _ = create_access_token(uuid.uuid4(), 0, TEST_JWT_SECRET)
    assert_401(auth_client, bearer(token))


def test_done_when_deactivated_user_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    token, user_id = token_for(auth_client, auth_db)
    assert auth_client.get("/v1/auth/me", headers=bearer(token)).status_code == 200
    auth_db.execute("UPDATE users SET is_active = false WHERE id = %s", (user_id,))
    assert_401(auth_client, bearer(token))


def test_done_when_bumped_token_version_is_401(auth_client: TestClient, auth_db: AuthDb) -> None:
    token, user_id = token_for(auth_client, auth_db)
    assert auth_client.get("/v1/auth/me", headers=bearer(token)).status_code == 200
    auth_db.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user_id,))
    assert_401(auth_client, bearer(token))
    # A fresh login carries the new version and works again.
    [(email,)] = auth_db.execute("SELECT email FROM users WHERE id = %s", (user_id,))
    fresh = login(auth_client, email, "correct horse battery staple").json()["access_token"]
    assert auth_client.get("/v1/auth/me", headers=bearer(fresh)).status_code == 200


def test_agent_key_is_not_a_user_token(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    agent = auth_db.make_agent(user.id)
    assert_401(auth_client, bearer(agent.key))
