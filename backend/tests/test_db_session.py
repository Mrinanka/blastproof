"""SEC-09: the running app refuses to use any database role but blastproof_app.

Also: production refuses to start without its database URL and JWT secret, and with no database
configured the auth routes answer 503 instead of crashing.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import WrongAppRoleError, check_app_role
from app.logging import configure_logging
from app.main import MissingProductionSettingError, create_app
from tests.conftest import (
    BLASTPROOF_OWNER_URL,
    TEST_JWT_SECRET,
    AuthDb,
    asyncpg_url,
    login,
    on_database,
)


def test_only_the_app_role_passes() -> None:
    check_app_role("blastproof_app")
    for role in ("blastproof_owner", "blastproof", "postgres"):
        with pytest.raises(WrongAppRoleError, match="must connect as blastproof_app"):
            check_app_role(role)


@pytest.mark.parametrize(
    ("update", "missing"),
    [
        ({"jwt_secret": None}, "JWT_SECRET"),
        ({"blastproof_database_url": None}, "BLASTPROOF_DATABASE_URL"),
    ],
)
def test_production_refuses_to_start_without_auth_settings(update: dict, missing: str) -> None:
    complete = Settings(
        _env_file=None,
        app_env="production",
        blastproof_database_url="postgresql+asyncpg://blastproof_app:x@db.invalid/blastproof",
        jwt_secret=TEST_JWT_SECRET,
    )
    create_app(complete)  # builds; never connects
    with pytest.raises(MissingProductionSettingError, match=missing):
        create_app(complete.model_copy(update=update))


def test_without_a_database_auth_routes_answer_503(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/auth/login", json={"email": "a@b.co", "password": "x"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
        assert client.get("/v1/auth/me", headers={"Authorization": "Bearer x"}).status_code == 503


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
def test_app_pointed_at_the_owner_role_serves_nothing(
    auth_db: AuthDb, capsys: pytest.CaptureFixture[str]
) -> None:
    user = auth_db.make_user()
    settings = Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=asyncpg_url(on_database(BLASTPROOF_OWNER_URL, auth_db.name)),
        jwt_secret=TEST_JWT_SECRET,
    )
    app = create_app(settings)
    configure_logging(stream=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = login(client, user.email, user.password)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert '"wrong_app_role"' in capsys.readouterr().out
    # Nothing was read or written as the owner.
    assert auth_db.execute("SELECT 1 FROM login_attempts WHERE email = %s", (user.email,)) == []
