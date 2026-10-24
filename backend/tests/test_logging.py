"""BE-01, OBS-02: every log line is one JSON object, including standard-library records."""

import io
import json
import logging
import logging.config
from datetime import datetime
from pathlib import Path

import pytest
import structlog

from app.logging import configure_logging

BACKEND_DIR = Path(__file__).resolve().parent.parent


def log_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_structlog_line_is_one_json_object() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    structlog.get_logger().info("hello", answer=42)

    [line] = log_lines(buf)
    assert line["event"] == "hello"
    assert line["level"] == "info"
    assert line["answer"] == 42
    assert line["timestamp"].endswith("Z")
    datetime.fromisoformat(line["timestamp"])


def test_stdlib_and_uvicorn_records_are_json() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 123)

    [line] = log_lines(buf)
    assert line["event"] == "Started server process [123]"
    assert line["level"] == "info"
    assert line["logger"] == "uvicorn.error"
    assert line["timestamp"].endswith("Z")


def test_uvicorn_access_log_is_quiet() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    logging.getLogger("uvicorn.access").info("GET /health 200")
    assert log_lines(buf) == []


def test_exception_is_logged_as_text() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("failed")
        logging.getLogger("some.library").exception("library failed")

    structlog_line, stdlib_line = log_lines(buf)
    for line in (structlog_line, stdlib_line):
        assert line["level"] == "error"
        assert "ValueError: boom" in line["exception"]


def test_reconfiguring_does_not_duplicate_lines() -> None:
    first, second = io.StringIO(), io.StringIO()
    configure_logging(stream=first)
    configure_logging(stream=second)
    structlog.get_logger().info("once")
    assert first.getvalue() == ""
    assert len(log_lines(second)) == 1


def test_uvicorn_log_config_file_gives_json(capsys: pytest.CaptureFixture[str]) -> None:
    # log_config.json is what uvicorn's reloader process uses before it ever imports the app.
    config = json.loads((BACKEND_DIR / "log_config.json").read_text(encoding="utf-8"))
    logging.config.dictConfig(config)
    logging.getLogger("uvicorn.error").info("Will watch for changes")
    logging.getLogger("uvicorn.access").info("GET /health 200")

    [line] = [json.loads(out) for out in capsys.readouterr().out.splitlines()]
    assert line["event"] == "Will watch for changes"
    assert line["logger"] == "uvicorn.error"
    assert line["timestamp"].endswith("Z")

    # The app's own setup then replaces that handler instead of adding a second one.
    buf = io.StringIO()
    configure_logging(stream=buf)
    structlog.get_logger().info("once")
    assert capsys.readouterr().out == ""
    assert len(log_lines(buf)) == 1
