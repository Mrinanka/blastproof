"""BE-23: /ready also checks the Neon API when shadow copies are Neon branches (step 10)."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.clock import SystemClock
from app.logging import configure_logging
from app.main import create_app
from app.services.neon import NeonApiClient
from tests.conftest import COMPANY_READER_URL, AuthDb, mcp_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

API_KEY = "napi_ready_secret_key"
WRITER = "postgresql://blastproof_writer:x@localhost:5433/company_main"


def neon_app(auth_db: AuthDb, transport: httpx.MockTransport) -> FastAPI:
    settings = mcp_settings(
        auth_db,
        company_connection_profile="neon",
        company_db_reader_url=COMPANY_READER_URL,
        neon_client="api",
        neon_api_key=API_KEY,
        neon_project_id="proj-1",
        neon_parent_branch_id="br-parent",
        company_db_writer_url=WRITER,
    )
    app = create_app(settings)
    app.state.neon_clients["neon"] = NeonApiClient(
        api_key=API_KEY,
        project_id="proj-1",
        parent_branch_id="br-parent",
        writer_url=WRITER,
        clock=SystemClock(),
        transport=transport,
        retry_delays=(),
    )
    return app


def test_neon_up_is_ready(auth_db: AuthDb) -> None:
    seen: list[httpx.Request] = []

    def up(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"project": {"name": "p"}})

    with TestClient(neon_app(auth_db, httpx.MockTransport(up))) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {
        "blastproof_db": "ok",
        "company_db": "ok",
        "neon_api": "ok",
    }
    assert [r.url.path for r in seen] == ["/api/v2/projects/proj-1"]


@pytest.mark.parametrize("status", [401, 500, 503])
def test_neon_down_is_not_ready_and_names_neon_api(
    auth_db: AuthDb, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": f"nope {API_KEY}"})

    app = neon_app(auth_db, httpx.MockTransport(down))
    configure_logging()
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Not ready: neon_api."
    captured = capsys.readouterr()
    assert API_KEY not in response.text + captured.out + captured.err


def test_the_local_profile_does_not_check_neon(auth_db: AuthDb) -> None:
    with TestClient(create_app(mcp_settings(auth_db))) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert "neon_api" not in response.json()["checks"]
