"""D-100, D-110, SEC-14: the MCP SDK and sqlglot never write raw values into our logs."""

import io
import json
import logging

import sqlglot

from app.config import Settings
from app.logging import configure_logging
from app.main import create_app

EMAIL = "alice@example.com"
URL_WITH_PASSWORD = "postgres://u:hunter2-pw@db.internal/company"


def log_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_create_app_leaves_no_rich_handler(settings: Settings) -> None:
    # MCPServer() calls logging.basicConfig(RichHandler); our handler must already be in place.
    create_app(settings)
    names = [type(handler).__name__ for handler in logging.getLogger().handlers]
    assert "RichHandler" not in names


def test_mcp_exception_record_keeps_only_its_format_string() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    logger = logging.getLogger("mcp.server.mcpserver.server")
    try:
        raise RuntimeError(f'invalid input syntax: "{EMAIL}" {URL_WITH_PASSWORD}')
    except RuntimeError:
        logger.exception("Tool %r raised an unexpected exception", f"query {EMAIL}")

    [line] = log_lines(buf)
    text = json.dumps(line)
    assert EMAIL not in text
    assert "hunter2-pw" not in text
    assert line["event"] == "Tool %r raised an unexpected exception"
    assert line["exc_type"] == "RuntimeError"
    assert "exception" not in line
    assert line["level"] == "error"


def test_mcp_info_records_are_dropped() -> None:
    # "Tool %r failed: %r" logs the tool's error text at INFO.
    buf = io.StringIO()
    configure_logging(stream=buf)
    logging.getLogger("mcp.server.mcpserver.server").info("Tool %r failed: %r", "x", EMAIL)
    assert log_lines(buf) == []


def test_other_libraries_keep_their_exceptions() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("mcpx.not.the.sdk").exception("failed %s", "here")
    [line] = log_lines(buf)
    assert line["event"] == "failed here"
    assert "ValueError: boom" in line["exception"]


def test_sqlglot_warnings_never_echo_sql() -> None:
    buf = io.StringIO()
    configure_logging(stream=buf)
    # sqlglot warns "'<sql>' contains unsupported syntax" for statements it parses as a Command.
    sqlglot.parse(f"EXPLAIN SELECT * FROM users WHERE email = '{EMAIL}'", read="postgres")
    assert EMAIL not in buf.getvalue()
