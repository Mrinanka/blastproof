"""CONV-03, CONV-05, BE-119: unknown routes and wrong methods use the error envelope, /docs works,
and every /v1 route declares the model it returns. No Docker."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_unknown_path_gets_the_envelope(client: TestClient) -> None:
    response = client.get("/v1/nothing-here")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "No such route."}}


def test_wrong_method_gets_the_envelope_and_keeps_allow(client: TestClient) -> None:
    response = client.delete("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


def test_docs_and_openapi_answer(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_every_v1_operation_declares_its_success_model(settings: Settings) -> None:
    paths = create_app(settings).openapi()["paths"]
    checked = 0
    for path, operations in paths.items():
        if not path.startswith("/v1/"):
            continue
        for method, operation in operations.items():
            success = [code for code in operation["responses"] if code.startswith("2")]
            assert success, (method, path)
            for code in success:
                schema = operation["responses"][code]["content"]["application/json"]["schema"]
                assert "$ref" in schema, (method, path, code)
            checked += 1
    assert checked == 19  # 7 earlier routes and the 12 of step 19


def test_error_responses_are_declared_as_the_envelope(settings: Settings) -> None:
    paths = create_app(settings).openapi()["paths"]
    ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for path, operations in paths.items():
        if not path.startswith("/v1/"):
            continue
        for method, operation in operations.items():
            for code, response in operation["responses"].items():
                if code.startswith(("4", "5")):
                    assert response["content"]["application/json"]["schema"] == ref, (
                        method,
                        path,
                        code,
                    )
