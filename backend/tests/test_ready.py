"""CONV-03, BE-23 (partly): /ready checks Blastproof's database and the company database.

The Neon API check comes with the Neon client in step 10 (D-105).
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.logging import configure_logging
from app.main import create_app
from tests.conftest import AuthDb, mcp_settings

DOWN_APP_DB = "postgresql+asyncpg://blastproof_app:secret-app-pw@127.0.0.1:1/blastproof"
DOWN_COMPANY_DB = "postgresql://blastproof_reader:secret-reader-pw@127.0.0.1:1/company_main"


def test_nothing_configured_is_not_ready(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "not_ready", "message": "Not ready: blastproof_db, company_db."}
    }


def test_ready_is_in_openapi(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        operation = client.get("/openapi.json").json()["paths"]["/ready"]["get"]
    assert set(operation["responses"]) >= {"200", "503"}


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
def test_all_up_is_ready(auth_db: AuthDb) -> None:
    with TestClient(create_app(mcp_settings(auth_db))) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"blastproof_db": "ok", "company_db": "ok"},
    }


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded")
@pytest.mark.parametrize(
    ("overrides", "failed"),
    [
        ({"blastproof_database_url": DOWN_APP_DB}, "blastproof_db"),
        ({"local_company_reader_url": DOWN_COMPANY_DB}, "company_db"),
        ({"local_company_reader_url": None}, "company_db"),
    ],
)
def test_a_dependency_down_is_not_ready_without_details(
    auth_db: AuthDb, capsys: pytest.CaptureFixture[str], overrides: dict, failed: str
) -> None:
    app = create_app(mcp_settings(auth_db, **overrides))
    configure_logging()  # onto this test's captured stdout
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "not_ready", "message": f"Not ready: {failed}."}}
    output = capsys.readouterr().out
    assert '"event": "not_ready"' in output
    for secret in ("secret-app-pw", "secret-reader-pw", "127.0.0.1"):
        assert secret not in response.text
        assert secret not in output
