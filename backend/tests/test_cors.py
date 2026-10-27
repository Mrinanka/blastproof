"""SEC-22 (code half): the API sends no CORS headers. The dashboard's browser code only calls its
own server, which forwards to this API, so no origin is ever allowed. No Docker."""

from fastapi.testclient import TestClient

ORIGIN = {"Origin": "https://evil.example.com"}


def test_a_preflight_gets_no_cors_headers(client: TestClient) -> None:
    response = client.options(
        "/v1/actions",
        headers=ORIGIN | {"Access-Control-Request-Method": "GET"},
    )
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


def test_a_request_with_an_origin_gets_no_cors_headers(client: TestClient) -> None:
    for path in ("/health", "/v1/actions", "/nope"):
        response = client.get(path, headers=ORIGIN)
        assert not [h for h in response.headers if h.lower().startswith("access-control-")], path
