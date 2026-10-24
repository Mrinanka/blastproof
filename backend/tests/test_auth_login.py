"""SEC-03, SEC-04, BE-108: POST /v1/auth/login against a migrated scratch database."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.security import decode_access_token
from app.logging import configure_logging
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET, AuthDb, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

INVALID = {"error": {"code": "invalid_credentials", "message": "Email or password is incorrect."}}


def test_login_returns_an_eight_hour_token_and_the_user(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    user = auth_db.make_user(role="admin")
    before = datetime.now(UTC).replace(microsecond=0)
    response = login(auth_client, user.email, user.password)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {
        "id": str(user.id),
        "email": user.email,
        "display_name": f"User {user.email[:10]}",
        "role": "admin",
    }
    claims = decode_access_token(body["access_token"], TEST_JWT_SECRET)
    assert claims.user_id == user.id
    assert claims.token_version == 0
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at.utcoffset() == timedelta(0)
    assert before + timedelta(hours=8) <= expires_at <= datetime.now(UTC) + timedelta(hours=8)
    payload = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert payload["exp"] == int(expires_at.timestamp())
    assert auth_db.execute(
        "SELECT ip::text, succeeded FROM login_attempts WHERE email = %s", (user.email,)
    ) == [("203.0.113.1/32", True)]


def test_email_case_and_spaces_are_ignored(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    assert login(auth_client, f"  {user.email.upper()} ", user.password).status_code == 200


def test_wrong_password_unknown_email_and_inactive_user_look_the_same(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    user = auth_db.make_user()
    inactive = auth_db.make_user(is_active=False)
    unknown = "nobody-here@example.com"
    responses = [
        login(auth_client, user.email, "not the password"),
        login(auth_client, unknown, "whatever password"),
        login(auth_client, inactive.email, inactive.password),
    ]
    for response in responses:
        assert response.status_code == 401
        assert response.json() == INVALID
        assert response.headers["www-authenticate"] == "Bearer"
        assert "access_token" not in response.text
    # Every one counts as a failure, so guessing emails is limited too.
    assert [auth_db.failures(e) for e in (user.email, unknown, inactive.email)] == [1, 1, 1]


def test_login_validates_its_body(auth_client: TestClient) -> None:
    too_long = {"email": "a@example.com", "password": "x" * 257}
    assert auth_client.post("/v1/auth/login", json=too_long).status_code == 422
    assert auth_client.post("/v1/auth/login", json={"email": "a@example.com"}).status_code == 422


def test_login_without_a_jwt_secret_is_unavailable_and_records_nothing(
    auth_settings: Settings, auth_db: AuthDb
) -> None:
    user = auth_db.make_user()
    settings = auth_settings.model_copy(update={"jwt_secret": None})
    with TestClient(create_app(settings)) as client:
        response = login(client, user.email, user.password)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert auth_db.execute("SELECT 1 FROM login_attempts WHERE email = %s", (user.email,)) == []


def test_logs_never_hold_the_password_token_or_email(
    auth_client: TestClient, auth_db: AuthDb, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(stream=None)  # the handler writes to the (captured) sys.stdout
    user = auth_db.make_user()
    ok = login(auth_client, user.email, user.password)
    login(auth_client, user.email, "a wrong password value")
    output = capsys.readouterr().out
    assert '"login_succeeded"' in output
    assert '"login_failed"' in output
    for secret in (user.password, "a wrong password value", ok.json()["access_token"], user.email):
        assert secret not in output


def test_openapi_declares_the_auth_models(auth_client: TestClient) -> None:
    schema = auth_client.get("/openapi.json").json()
    login_ok = schema["paths"]["/v1/auth/login"]["post"]["responses"]["200"]
    assert login_ok["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LoginResponse"
    }
    me = schema["paths"]["/v1/auth/me"]["get"]
    assert me["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UserView"
    }
    assert me["security"] == [{"UserToken": []}]
    assert schema["components"]["securitySchemes"]["UserToken"]["scheme"] == "bearer"
    error = schema["paths"]["/v1/auth/login"]["post"]["responses"]["429"]
    assert error["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_rejected_login_body_never_repeats_the_password(auth_client: TestClient) -> None:
    password = "PASSWORD-SENT-" + "x" * 300
    response = auth_client.post(
        "/v1/auth/login", json={"email": "someone@example.com", "password": password}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "PASSWORD-SENT-" not in response.text
