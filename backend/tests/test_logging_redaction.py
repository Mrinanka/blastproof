"""SEC-14, SEC-15: the log formatter redacts every line, whoever wrote it."""

import io
import json
import logging
import logging.config
from pathlib import Path

import pytest
import structlog

from app.logging import configure_logging
from app.services.redaction import set_known_secrets

BACKEND_DIR = Path(__file__).resolve().parent.parent
DSN = "postgresql://app:hunter2hunter2@ep-x.neon.tech/db"


@pytest.fixture(autouse=True)
def _clean_registry():
    set_known_secrets([])
    yield
    set_known_secrets([])


def log_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_structlog_fields_are_redacted() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    structlog.get_logger().info(
        "saw ana@example.com", detail=DSN, nested={"phone": "+1 (415) 555-0132"}, count=3
    )
    raw = buf.getvalue()
    for planted in ("ana@example.com", "hunter2hunter2", "555-0132"):
        assert planted not in raw
    [line] = log_lines(buf)
    assert line["count"] == 3
    assert isinstance(line["count"], int)


def test_known_secrets_given_to_configure_logging_are_redacted() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf, secrets=["a-long-random-secret-value"])
    structlog.get_logger().info("x", detail="used a-long-random-secret-value here")
    assert "a-long-random-secret-value" not in buf.getvalue()


def test_third_party_traceback_is_redacted() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    try:
        raise RuntimeError(f"could not connect to {DSN} for ana@example.com")
    except RuntimeError:
        logging.getLogger("thirdparty").exception("connect failed for bob@example.com")
    raw = buf.getvalue()
    for planted in ("hunter2hunter2", "ana@example.com", "bob@example.com"):
        assert planted not in raw
    [line] = log_lines(buf)
    assert "RuntimeError" in line["exception"]


def test_structlog_traceback_is_redacted() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    try:
        raise ValueError(f"bad {DSN}")
    except ValueError:
        structlog.get_logger().exception("failed")
    assert "hunter2hunter2" not in buf.getvalue()


def test_uvicorn_record_is_redacted() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    logging.getLogger("uvicorn.error").warning("client ana@example.com sent %s", DSN)
    raw = buf.getvalue()
    assert "ana@example.com" not in raw
    assert "hunter2hunter2" not in raw


@pytest.mark.parametrize("name", ["httpx", "httpcore", "httpcore.http11", "httpx2"])
def test_http_client_info_lines_are_dropped(name: str) -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    logging.getLogger(name).info("HTTP Request: POST https://console.neon.tech/api/v2/projects/p")
    assert log_lines(buf) == []
    logging.getLogger(name).warning("a real problem")
    assert len(log_lines(buf)) == 1


def test_reloader_handler_from_log_config_also_redacts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = json.loads((BACKEND_DIR / "log_config.json").read_text(encoding="utf-8"))
    logging.config.dictConfig(config)
    logging.getLogger("uvicorn.error").info("watching for ana@example.com via %s", DSN)
    out = capsys.readouterr().out
    assert "ana@example.com" not in out
    assert "hunter2hunter2" not in out
    assert "watching" in out


def test_numbers_and_ids_pass_through() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    structlog.get_logger().info(
        "stage_done",
        stage="shadow",
        duration_ms=10423,
        action_id="3f2b8c1e-5a4d-4e7f-9b6a-1c2d3e4f5a6b",
        request_id="9d1c2b3a-1111-4222-8333-444455556666",
    )
    [line] = log_lines(buf)
    assert line["duration_ms"] == 10423
    assert line["action_id"] == "3f2b8c1e-5a4d-4e7f-9b6a-1c2d3e4f5a6b"
    assert line["request_id"] == "9d1c2b3a-1111-4222-8333-444455556666"
