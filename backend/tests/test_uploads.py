"""SEC-32 (backend half): how an upload is read and refused. No Docker.

The routes' admin check and the real ingest are in test_knowledge_api.py.
"""

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from app.services import uploads
from app.services.knowledge import MAX_DOCUMENT_BYTES


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)

    @app.post("/test/upload")
    async def upload_route(request: Request) -> dict[str, object]:
        upload = await uploads.read_upload(request)
        return {"name": upload.name, "size": len(upload.data), "tables": list(upload.tables)}

    return TestClient(app)


def post(client: TestClient, files: list[tuple[str, tuple[str, bytes]]], data: dict | None = None):
    return client.post("/test/upload", files=files, data=data)


def one(name: str = "notes.md", body: bytes = b"# Title\ntext") -> list:
    return [("file", (name, body))]


def test_a_good_upload_is_read(client: TestClient) -> None:
    response = post(client, one("Refund rules.txt"), {"tables": ["Users", "public.orders"]})
    assert response.status_code == 200
    assert response.json() == {
        "name": "Refund rules.txt",
        "size": 12,
        "tables": ["users", "orders"],
    }


async def test_an_oversize_length_is_refused_before_any_byte_is_read() -> None:
    async def receive() -> dict:
        raise AssertionError("the body was read")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", str(uploads.MAX_BODY_BYTES + 1).encode()),
        ],
    }
    with pytest.raises(ApiError) as caught:
        await uploads.read_upload(Request(scope, receive))
    assert (caught.value.status_code, caught.value.code) == (413, "too_large")


def test_a_missing_length_is_411() -> None:
    with pytest.raises(ApiError) as caught:
        uploads.check_length(None)
    assert (caught.value.status_code, caught.value.code) == (411, "length_required")


def test_the_length_limit_boundary() -> None:
    uploads.check_length(str(uploads.MAX_BODY_BYTES))
    with pytest.raises(ApiError):
        uploads.check_length(str(uploads.MAX_BODY_BYTES + 1))
    with pytest.raises(ApiError) as caught:
        uploads.check_length("12abc")
    assert caught.value.status_code == 422


def test_exactly_one_megabyte_passes_and_one_byte_more_fails(client: TestClient) -> None:
    assert post(client, one(body=b"a" * MAX_DOCUMENT_BYTES)).status_code == 200
    over = post(client, one(body=b"a" * (MAX_DOCUMENT_BYTES + 1)))
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "invalid_upload"


def test_no_file_and_two_files_are_refused(client: TestClient) -> None:
    assert client.post("/test/upload", data={"tables": "users"}).status_code == 422
    two = post(client, [*one("a.md"), *one("b.md")])
    assert two.status_code == 422
    assert two.json()["error"]["code"] == "invalid_upload"


@pytest.mark.parametrize(
    "name",
    [
        "../x.md",
        "a/b.md",
        "a\\b.md",
        "x.pdf",
        "x",
        ".hidden.md",
        "schema_scan",
        "a" * 101 + ".md",
        "bad<name>.md",
        "",
    ],
)
def test_bad_names_are_refused(client: TestClient, name: str) -> None:
    response = post(client, one(name))
    assert response.status_code == 422, name
    assert response.json()["error"]["code"] == "invalid_upload"


def test_a_refusal_never_repeats_the_file_name(client: TestClient) -> None:
    response = post(client, one("confidential-plan.pdf"))
    assert response.status_code == 422
    assert "confidential" not in response.text


def test_the_reserved_and_the_docs_names(client: TestClient) -> None:
    assert post(client, one("schema_docs.md")).status_code == 200  # the way to update it


def test_text_that_is_not_utf8_is_refused_later_by_the_splitter() -> None:
    # read_upload returns the bytes; the route decodes them (DocumentError -> 422).
    from app.services.knowledge import DocumentError, split_document

    with pytest.raises(DocumentError):
        split_document("x.md", b"\xff\xfe\x00bad")


def test_more_than_twenty_tables_and_a_bad_tag_are_refused(client: TestClient) -> None:
    many = post(client, one(), {"tables": [f"t{i}" for i in range(21)]})
    assert many.status_code == 422
    assert post(client, one(), {"tables": [f"t{i}" for i in range(20)]}).status_code == 200
    bad = post(client, one(), {"tables": ["users; drop"]})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_upload"


def test_a_body_that_is_not_multipart_is_refused(client: TestClient) -> None:
    response = client.post(
        "/test/upload",
        content=b"not a form",
        headers={"Content-Type": "multipart/form-data; boundary=zzz"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_upload"
