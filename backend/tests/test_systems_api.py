"""BE-117: systems list and schema rescan. Needs Docker (the company database too)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.action_helpers import make_system, token_for
from tests.admin_api_helpers import build_admin_app, client_for
from tests.conftest import AuthDb

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


@pytest.fixture
def client(auth_db: AuthDb) -> Iterator[TestClient]:
    auth_db.execute("DELETE FROM knowledge_chunks")
    yield from client_for(build_admin_app(auth_db))


def test_list_is_by_name_and_hides_neon_ids(client: TestClient, auth_db: AuthDb) -> None:
    member = auth_db.make_user()
    make_system(auth_db)
    body = client.get("/v1/systems", headers=token_for(member)).json()
    assert body["total"] >= 1
    names = [item["name"] for item in body["items"]]
    assert names == sorted(names)
    assert set(body["items"][0]) == {"id", "name", "kind", "connection_profile"}


def test_a_scan_rebuilds_one_chunk_per_table(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    system_id = make_system(auth_db)
    first = client.post(f"/v1/systems/{system_id}/scan", headers=token_for(admin))
    assert first.status_code == 200
    body = first.json()
    assert body["system_id"] == str(system_id) and body["source"] == "schema_scan"
    assert body["chunks"] >= 8  # the demo company's tables
    [(stored,)] = auth_db.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE source = 'schema_scan'"
    )
    assert stored == body["chunks"]
    again = client.post(f"/v1/systems/{system_id}/scan", headers=token_for(admin)).json()
    [(stored,)] = auth_db.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE source = 'schema_scan'"
    )
    assert stored == again["chunks"] == body["chunks"]  # replaced, not doubled


def test_unknown_and_malformed_ids_are_404(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    for system_id in (uuid.uuid4(), "not-a-uuid"):
        response = client.post(f"/v1/systems/{system_id}/scan", headers=token_for(admin))
        assert response.status_code == 404


def test_members_cannot_scan(client: TestClient, auth_db: AuthDb) -> None:
    system_id = make_system(auth_db)
    response = client.post(f"/v1/systems/{system_id}/scan", headers=token_for(auth_db.make_user()))
    assert response.status_code == 403


def test_a_profile_without_a_reader_url_is_503_with_no_fallback(
    client: TestClient, auth_db: AuthDb
) -> None:
    """The app has the local reader only; a `neon` system must not be scanned through it."""
    admin = auth_db.make_user(role="admin")
    [(system_id,)] = auth_db.execute(
        "INSERT INTO systems (name, connection_profile) VALUES (%s, 'neon') RETURNING id",
        (f"neon-{uuid.uuid4().hex[:8]}",),
    )
    response = client.post(f"/v1/systems/{system_id}/scan", headers=token_for(admin))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    [(stored,)] = auth_db.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE source = 'schema_scan'"
    )
    assert stored == 0
