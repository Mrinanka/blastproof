"""BE-118: users list and create, admin only. Needs Docker."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.action_helpers import token_for
from tests.admin_api_helpers import at_debug, build_admin_app, client_for, logged_text
from tests.conftest import AuthDb, login

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

PASSWORD = "a long enough password"


@pytest.fixture
def client(auth_db: AuthDb) -> Iterator[TestClient]:
    yield from client_for(build_admin_app(auth_db))


def new_user(client, admin, **fields):
    body = {
        "email": f"new-{uuid.uuid4().hex[:10]}@example.com",
        "display_name": "New Person",
        "password": PASSWORD,
    } | fields
    return client.post("/v1/users", headers=token_for(admin), json=body), body


def test_an_admin_creates_a_user_who_can_log_in(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    response, sent = new_user(client, admin)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == sent["email"] and body["role"] == "member"
    assert body["is_active"] is True
    assert set(body) == {"id", "email", "display_name", "role", "is_active", "created_at"}
    assert PASSWORD not in response.text and "password" not in body
    assert login(client, sent["email"], PASSWORD).status_code == 200


def test_an_admin_can_create_an_admin(client: TestClient, auth_db: AuthDb) -> None:
    response, _ = new_user(client, auth_db.make_user(role="admin"), role="admin")
    assert response.status_code == 201 and response.json()["role"] == "admin"


def test_a_duplicate_email_is_409_whatever_the_case_or_spaces(
    client: TestClient, auth_db: AuthDb
) -> None:
    admin = auth_db.make_user(role="admin")
    _, sent = new_user(client, admin)
    for email in (sent["email"], sent["email"].upper(), f"  {sent['email']}  "):
        response = client.post("/v1/users", headers=token_for(admin), json=sent | {"email": email})
        assert response.status_code == 409, email
        assert response.json()["error"]["code"] == "email_taken"


def test_two_creates_at_once_leave_one_user_and_a_409(client: TestClient, auth_db: AuthDb) -> None:
    from concurrent.futures import ThreadPoolExecutor

    admin = auth_db.make_user(role="admin")
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    body = {"email": email, "display_name": "Race", "password": PASSWORD}
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(
            pool.map(
                lambda _: client.post("/v1/users", headers=token_for(admin), json=body).status_code,
                range(2),
            )
        )
    assert codes == [201, 409]
    [(count,)] = auth_db.execute("SELECT count(*) FROM users WHERE email = %s", (email,))
    assert count == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"password": "short"},
        {"email": "not-an-email"},
        {"display_name": "   "},
        {"role": "owner"},
    ],
)
def test_bad_input_is_422(client: TestClient, auth_db: AuthDb, fields: dict) -> None:
    response, _ = new_user(client, auth_db.make_user(role="admin"), **fields)
    assert response.status_code == 422
    assert PASSWORD not in response.text and "short" not in response.text


def test_a_short_password_gives_invalid_account(client: TestClient, auth_db: AuthDb) -> None:
    response, _ = new_user(client, auth_db.make_user(role="admin"), password="short")
    assert response.json()["error"]["code"] == "invalid_account"


def test_the_password_is_not_logged(
    client: TestClient, auth_db: AuthDb, caplog: pytest.LogCaptureFixture
) -> None:
    at_debug(caplog)
    admin = auth_db.make_user(role="admin")
    new_user(client, admin, password="a-distinctive-SECRET-passphrase")
    new_user(client, admin, password="short-SECRET")
    assert "SECRET" not in logged_text(caplog)


def test_the_list_is_newest_first_and_paged(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    first, _ = new_user(client, admin)
    second, _ = new_user(client, admin)
    page = client.get("/v1/users?limit=2", headers=token_for(admin)).json()
    assert [i["id"] for i in page["items"]] == [second.json()["id"], first.json()["id"]]
    assert page["total"] >= 3 and page["limit"] == 2
    assert all("password" not in item for item in page["items"])


def test_members_get_403_on_both_routes(client: TestClient, auth_db: AuthDb) -> None:
    member = auth_db.make_user()
    assert client.get("/v1/users", headers=token_for(member)).status_code == 403
    response, _ = new_user(client, member)
    assert response.status_code == 403


def test_anonymous_gets_401(client: TestClient) -> None:
    assert client.get("/v1/users").status_code == 401
