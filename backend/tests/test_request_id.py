"""OBS-04, OBS-02, CONV-09: X-Request-ID on every response, and the same ID on its log lines."""

import io
import json
import uuid

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.config import Settings
from app.core.request_id import RequestIdMiddleware, parse_request_id
from app.logging import configure_logging
from app.main import create_app


def log_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_response_has_uuid_request_id(client: TestClient) -> None:
    response = client.get("/health")
    request_id = response.headers["X-Request-ID"]
    assert str(uuid.UUID(request_id)) == request_id


def test_unknown_route_also_gets_request_id(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404
    uuid.UUID(response.headers["X-Request-ID"])


def test_valid_incoming_uuid_is_echoed(client: TestClient) -> None:
    incoming = str(uuid.uuid4())
    response = client.get("/health", headers={"X-Request-ID": incoming})
    assert response.headers["X-Request-ID"] == incoming


@pytest.mark.parametrize("incoming", ["not-a-uuid", "x" * 500, "12345"])
def test_invalid_incoming_value_is_replaced(client: TestClient, incoming: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": incoming})
    request_id = response.headers["X-Request-ID"]
    assert request_id != incoming
    assert str(uuid.UUID(request_id)) == request_id


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"",
        b"abc\r\nX-Injected: 1",
        b"6f1c2c3e-0d4b-4d8e-9a3c-2b1e4f5a6b7c\nfake log line",
        b"x" * 500,
        b"\xff\xfe",
    ],
)
def test_parse_request_id_rejects_unsafe_values(raw: bytes | None) -> None:
    # Newlines cannot be sent through the HTTP client, so the parser is tested directly.
    assert parse_request_id(raw) is None


def test_parse_request_id_returns_canonical_form() -> None:
    value = uuid.uuid4()
    assert parse_request_id(str(value).upper().encode()) == str(value)


def test_two_requests_get_different_ids(client: TestClient) -> None:
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]
    assert first != second


def test_request_log_carries_request_id(client: TestClient) -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)

    response = client.get("/health")

    [finished] = [line for line in log_lines(buf) if line["event"] == "request_finished"]
    assert finished["request_id"] == response.headers["X-Request-ID"]
    assert finished["method"] == "GET"
    assert finished["path"] == "/health"
    assert finished["status"] == 200
    assert isinstance(finished["duration_ms"], float)


def http_scope(path: str = "/x") -> Scope:
    return {"type": "http", "method": "GET", "path": path, "headers": []}


async def no_body() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_request_id_does_not_leak_after_request() -> None:
    # Called directly, in this task, so a missing clear would leak into the next log line.
    # (The test client runs requests in another thread, where a leak could never show.)
    async def ok_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def discard(message: Message) -> None:
        pass

    buf = io.StringIO()
    configure_logging(stream=buf)
    await RequestIdMiddleware(ok_app)(http_scope(), no_body, discard)
    structlog.get_logger().info("after_request")

    finished, after = log_lines(buf)
    assert finished["event"] == "request_finished"
    assert "request_id" in finished
    assert after["event"] == "after_request"
    assert "request_id" not in after


@pytest.fixture
def crashing_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail that must not reach the client")

    return app


def test_unhandled_exception_returns_500_with_request_id(crashing_app: FastAPI) -> None:
    buf = io.StringIO()
    with TestClient(crashing_app) as client:
        configure_logging(stream=buf)
        response = client.get("/boom")

    assert response.status_code == 500
    request_id = response.headers["X-Request-ID"]
    uuid.UUID(request_id)
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "secret detail" not in response.text

    lines = [line for line in log_lines(buf) if line.get("request_id") == request_id]
    error, finished = lines
    assert error["event"] == "unhandled_exception"
    assert error["level"] == "error"
    # Gate audit finding 6: the class and where, never the message (it can hold row values).
    assert error["exc_type"] == "RuntimeError"
    assert error["frames"][-1].endswith(" boom")
    assert "secret detail" not in buf.getvalue()
    assert finished["event"] == "request_finished"
    assert finished["status"] == 500


async def test_exception_after_response_started_is_logged_and_reraised() -> None:
    # Headers already went out, so no clean 500 is possible: log with the ID, let the server close.
    async def half_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("mid-stream failure")

    sent: list[Message] = []

    async def record(message: Message) -> None:
        sent.append(message)

    buf = io.StringIO()
    configure_logging(stream=buf)
    with pytest.raises(RuntimeError, match="mid-stream failure"):
        await RequestIdMiddleware(half_app)(http_scope(), no_body, record)

    assert [m["type"] for m in sent] == ["http.response.start"]
    error, finished = log_lines(buf)
    assert error["event"] == "unhandled_exception"
    assert error["request_id"] == finished["request_id"]
    assert finished["status"] == 200


# --- Gate audit finding 6: the crash log keeps where it failed, never what the error said -----

LEAKY = 'invalid input syntax for type integer: "alice@example.com"'


@pytest.fixture
def leaking_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    def load_customer() -> None:
        raise ValueError(LEAKY)

    @app.get("/leak")
    async def leak() -> None:
        try:
            load_customer()
        except ValueError as exc:
            raise RuntimeError(f"wrapped: {exc}") from exc

    return app


def test_the_crash_log_holds_no_error_text_but_says_where(leaking_app: FastAPI) -> None:
    """A Postgres error can carry row values; only the class and code locations are logged."""
    buf = io.StringIO()
    with TestClient(leaking_app) as client:
        configure_logging(stream=buf)
        assert client.get("/leak").status_code == 500

    assert "alice@example.com" not in buf.getvalue()
    assert "invalid input syntax" not in buf.getvalue()
    [error] = [line for line in log_lines(buf) if line["event"] == "unhandled_exception"]
    assert error["exc_type"] == "RuntimeError"
    assert "exception" not in error  # no formatted traceback, which repeats the message
    assert error["frames"][-1] == f"test_request_id.py:{_line_of('raise RuntimeError(f')} leak"
    assert not any(char in frame for frame in error["frames"] for char in "\n\"'")


def test_the_frames_are_capped() -> None:
    from app.core.request_id import MAX_LOGGED_FRAMES, crash_frames

    def recurse(n: int) -> None:
        if n == 0:
            raise RuntimeError("deep")
        recurse(n - 1)

    try:
        recurse(MAX_LOGGED_FRAMES * 2)
    except RuntimeError as exc:
        frames = crash_frames(exc)
    assert len(frames) == MAX_LOGGED_FRAMES
    assert frames[-1].endswith(" recurse")  # innermost last


def _line_of(text: str) -> int:
    from pathlib import Path

    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    [number] = [i for i, line in enumerate(lines, 1) if text in line and "_line_of" not in line]
    return number


def test_the_log_holds_the_route_pattern_not_the_path_parameters(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A path parameter can be a file name (DELETE /v1/knowledge/{source}), so it is not logged."""
    caplog.set_level("INFO")
    client.delete("/v1/knowledge/confidential-plan.md")
    text = " ".join(
        f"{rec.getMessage()}" for rec in caplog.records if rec.name == "app.core.request_id"
    )
    assert "/v1/knowledge/{source}" in text
    assert "confidential" not in text
