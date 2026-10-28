"""structlog JSON logging. Standard-library and uvicorn logs go through the same JSON formatter.

Two libraries can write raw values into our logs, so they are held back here (D-110):
- the MCP SDK (`mcp.*` loggers) logs tool exceptions with their text and traceback, which can hold
  an email or a connection string. Its records keep only their bare format string, with no
  arguments, traceback or stack, plus the exception's class as `exc_type`; the level is WARNING.
- sqlglot warns with the SQL it could not fully parse; only its errors are kept.
- httpx and httpcore log every request URL at INFO (Neon project and branch IDs, model endpoints);
  only their warnings and errors are kept.

Behind those, every line passes `redact_event_dict` after the traceback is formatted and before it
is rendered: secrets and personal data a call site or a third-party library let through are replaced
(SEC-14, SEC-15). The call-site allowlist stays the primary rule; this is the last layer.
"""

import logging
import sys
from collections.abc import Iterable
from typing import TextIO

import structlog

from app.services.redaction import redact_event_dict, set_known_secrets

# Marks the handler this module installs, so reconfiguring replaces it and leaves others alone.
_HANDLER_FLAG = "_blastproof_handler"

_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]

_MCP_LOGGER = "mcp"
_QUIET_LOGGERS = {
    _MCP_LOGGER: logging.WARNING,
    "sqlglot": logging.ERROR,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "httpx2": logging.WARNING,
}


class StripMcpDetails(logging.Filter):
    """Keep an `mcp.*` record's bare format string; drop its arguments, traceback and stack.

    On the handler, not a logger, so it also sees records that propagate up from child loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == _MCP_LOGGER or record.name.startswith(_MCP_LOGGER + "."):
            if record.exc_info and record.exc_info[0] is not None:
                record.exc_type = record.exc_info[0].__name__
            record.msg = str(record.msg)
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def make_handler(stream: TextIO | None = None) -> logging.Handler:
    """A handler that writes one JSON object per line.

    Also used by `log_config.json`, so uvicorn's reloader process (which never imports the app)
    logs JSON too.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*_SHARED_PROCESSORS, structlog.stdlib.ExtraAdder(allow=["exc_type"])],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_event_dict,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(StripMcpDetails())
    setattr(handler, _HANDLER_FLAG, True)
    return handler


def configure_logging(
    level: str = "INFO", stream: TextIO | None = None, secrets: Iterable[str] = ()
) -> None:
    """`secrets` are the values this process holds (`known_secrets_from`); the redactor replaces
    them wherever they appear in a log line."""
    set_known_secrets(secrets)
    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = make_handler(stream)

    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own plain-text handlers; send its records to the root handler instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # RequestIdMiddleware logs every request once, so uvicorn's access line would be a duplicate.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name, quiet_level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_level)
