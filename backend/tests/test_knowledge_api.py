"""BE-115, BE-116, SEC-32: knowledge routes. Needs Docker."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.action_helpers import token_for
from tests.admin_api_helpers import at_debug, build_admin_app, client_for, logged_text
from tests.conftest import AuthDb, mcp_settings, with_client_ip

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

DOC = b"# Refunds\nRefunds over 500 dollars need a manager.\n"
SECRET_NAME = "confidential-refund-policy.md"


@pytest.fixture
def app(auth_db: AuthDb):
    auth_db.execute("DELETE FROM knowledge_chunks")
    return build_admin_app(auth_db)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    yield from client_for(app)


def upload(client, user, name="notes.md", body=DOC, tables=()):
    return client.post(
        "/v1/knowledge",
        headers=token_for(user),
        files=[("file", (name, body))],
        data={"tables": list(tables)} if tables else None,
    )


def sources(client, user) -> dict[str, dict]:
    response = client.get("/v1/knowledge", headers=token_for(user))
    assert response.status_code == 200
    return {item["source"]: item for item in response.json()["items"]}


def test_upload_list_replace_delete(client: TestClient, auth_db: AuthDb) -> None:
    admin, member = auth_db.make_user(role="admin"), auth_db.make_user()
    created = upload(client, admin, "Refund rules.md", DOC, tables=["Orders"])
    assert created.status_code == 201
    assert created.json() == {"source": "Refund rules.md", "chunks": 1}

    listed = sources(client, member)  # everyone signed in may read the list
    assert listed["Refund rules.md"]["chunks"] == 1
    assert listed["Refund rules.md"]["kind"] == "upload"
    assert listed["Refund rules.md"]["table_names"] == ["orders"]

    longer = b"# One\nfirst\n\n# Two\nsecond\n"
    assert upload(client, admin, "Refund rules.md", longer).json()["chunks"] == 2  # replaces
    assert sources(client, admin)["Refund rules.md"]["chunks"] == 2
    assert sources(client, admin)["Refund rules.md"]["table_names"] == []

    deleted = client.delete("/v1/knowledge/Refund%20rules.md", headers=token_for(admin))
    assert deleted.status_code == 200
    assert deleted.json() == {"source": "Refund rules.md", "chunks": 2}
    assert "Refund rules.md" not in sources(client, admin)
    again = client.delete("/v1/knowledge/Refund%20rules.md", headers=token_for(admin))
    assert again.status_code == 404


def test_known_names_have_their_kind(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    assert upload(client, admin, "schema_docs.md", DOC).status_code == 201
    assert sources(client, admin)["schema_docs.md"]["kind"] == "schema_docs"


def test_an_unknown_source_is_404(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    assert client.delete("/v1/knowledge/nope.md", headers=token_for(admin)).status_code == 404


def test_paging(client: TestClient, auth_db: AuthDb) -> None:
    admin = auth_db.make_user(role="admin")
    for name in ("b.md", "a.md", "c.md"):
        upload(client, admin, name)
    page = client.get("/v1/knowledge?limit=2&offset=1", headers=token_for(admin)).json()
    assert [item["source"] for item in page["items"]] == ["b.md", "c.md"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert client.get("/v1/knowledge?limit=101", headers=token_for(admin)).status_code == 422


def test_a_table_tag_makes_retrieval_return_the_document(app, auth_db: AuthDb) -> None:
    """DW-F10, backend half: a document tagged `users` reaches an action on `users`."""
    admin = auth_db.make_user(role="admin")
    with TestClient(with_client_ip(app)) as client:
        assert upload(client, admin, "tagged.md", DOC, tables=["users"]).status_code == 201
        other = upload(client, admin, "untagged.md", b"# Other\nnothing about it\n")
        assert other.status_code == 201
        # Run on the app's own event loop, where its database connections live.
        retrieval = client.portal.call(app.state.knowledge.retrieve, "zzz unrelated", ["users"], 0)
    assert [c.source for c in retrieval.chunks] == ["tagged.md"]
    assert retrieval.chunks[0].match == "table"


def test_empty_and_blank_documents_are_refused_and_delete_nothing(
    client: TestClient, auth_db: AuthDb
) -> None:
    admin = auth_db.make_user(role="admin")
    assert upload(client, admin, "keep.md", DOC).status_code == 201
    for body in (b"", b"   \n\n  "):
        response = upload(client, admin, "keep.md", body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_upload"
    assert sources(client, admin)["keep.md"]["chunks"] == 1  # still there


def test_text_that_is_not_utf8_is_refused(client: TestClient, auth_db: AuthDb) -> None:
    response = upload(client, auth_db.make_user(role="admin"), "bad.md", b"\xff\xfe\x00\x01")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_upload"


def test_members_are_refused_before_the_size_is_looked_at(
    client: TestClient, auth_db: AuthDb
) -> None:
    member = auth_db.make_user()
    big = client.post(
        "/v1/knowledge",
        headers=token_for(member) | {"Content-Length": "99999999", "Content-Type": "multipart/x"},
        content=b"x",
    )
    assert big.status_code == 403  # the role is checked first, not 413
    assert upload(client, member).status_code == 403
    assert client.delete("/v1/knowledge/a.md", headers=token_for(member)).status_code == 403


async def test_an_admin_with_a_huge_length_gets_413_and_no_body_is_read(
    app, auth_db: AuthDb
) -> None:
    """Raw ASGI, because the test client sets a true Content-Length itself. `receive` fails the
    test if the app asks for a single byte of the body."""
    auth = token_for(auth_db.make_user(role="admin"))["Authorization"].encode()
    headers = [
        (b"authorization", auth),
        (b"content-type", b"multipart/form-data; boundary=x"),
        (b"content-length", b"99999999"),
        (b"host", b"testserver"),
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/knowledge",
        "raw_path": b"/v1/knowledge",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "app": app,
    }
    sent: list[dict] = []

    async def receive() -> dict:
        raise AssertionError("the app read the body")

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    assert sent[0]["status"] == 413
    assert b"too_large" in sent[1]["body"]


def test_no_knowledge_service_is_503(auth_db: AuthDb) -> None:
    app = create_app(mcp_settings(auth_db))
    app.state.knowledge = None
    with TestClient(with_client_ip(app)) as client:
        admin = auth_db.make_user(role="admin")
        assert upload(client, admin).status_code == 503
        assert client.delete("/v1/knowledge/a.md", headers=token_for(admin)).status_code == 503


def test_a_failing_embedder_is_503_and_changes_nothing(
    client: TestClient, app, auth_db: AuthDb
) -> None:
    from tests.knowledge_helpers import BrokenEmbedder

    admin = auth_db.make_user(role="admin")
    assert upload(client, admin, "stays.md", DOC).status_code == 201
    app.state.knowledge._embedder = BrokenEmbedder()
    failed = upload(client, admin, "stays.md", b"# New\ntext\n")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "unavailable"
    assert "secret@example.com" not in failed.text
    assert sources(client, admin)["stays.md"]["chunks"] == 1


def test_no_document_text_or_file_name_is_logged(
    client: TestClient, auth_db: AuthDb, caplog: pytest.LogCaptureFixture
) -> None:
    at_debug(caplog)
    admin = auth_db.make_user(role="admin")
    secret = b"# Notes\nthe launch code is HUNTER2-TEXT\n"
    upload(client, admin, SECRET_NAME, secret)
    client.delete(f"/v1/knowledge/{SECRET_NAME}", headers=token_for(admin))
    upload(client, admin, SECRET_NAME, b"")
    text = logged_text(caplog)
    assert "HUNTER2" not in text
    assert "confidential" not in text
