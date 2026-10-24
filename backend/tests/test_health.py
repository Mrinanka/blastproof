"""BE-01, DW-B01 (automated part): /health returns ok and declares its response model."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_declares_health_response_model(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    ok = schema["paths"]["/health"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
    health = schema["components"]["schemas"]["HealthResponse"]
    assert health["properties"]["status"]["const"] == "ok"
