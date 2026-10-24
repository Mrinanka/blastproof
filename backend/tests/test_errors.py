"""CONV-04 (validation part, brought forward from step 19): invalid input gets the error envelope
and the response never repeats what was sent (it may be a password)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.config import Settings
from app.main import create_app

SENT = "SENT-VALUE-"


class Body(BaseModel):
    name: str = Field(max_length=5)
    count: int


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)

    @app.post("/test/body")
    async def body_route(body: Body) -> dict[str, str]:
        return {"name": body.name}

    return TestClient(app)


def test_invalid_body_gets_the_envelope_without_the_input(client: TestClient) -> None:
    response = client.post("/test/body", json={"name": SENT * 3, "count": SENT})
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    message = body["error"]["message"]
    assert "body.name" in message
    assert "body.count" in message
    assert SENT not in response.text


def test_missing_field_and_bad_json(client: TestClient) -> None:
    missing = client.post("/test/body", json={"name": "ok"})
    assert missing.status_code == 422
    assert missing.json()["error"]["message"] == "Invalid request: body.count: Field required."

    bad_json = client.post(
        "/test/body",
        content=b'{"name": "' + SENT.encode(),
        headers={"content-type": "application/json"},
    )
    assert bad_json.status_code == 422
    assert bad_json.json()["error"]["code"] == "validation_error"
    assert SENT not in bad_json.text


def test_many_errors_are_cut_to_a_short_message(client: TestClient) -> None:
    response = client.post("/test/body", json=[SENT] * 50)
    assert response.status_code == 422
    assert len(response.json()["error"]["message"]) < 500
    assert SENT not in response.text


def test_openapi_documents_the_envelope_for_422(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    response_422 = schema["paths"]["/test/body"]["post"]["responses"]["422"]
    assert response_422["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
