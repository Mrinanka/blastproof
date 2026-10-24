"""structlog JSON logging. Standard-library and uvicorn logs go through the same JSON formatter."""

import logging
import sys
from typing import TextIO

import structlog

# Marks the handler this module installs, so reconfiguring replaces it and leaves others alone.
_HANDLER_FLAG = "_blastproof_handler"

_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def make_handler(stream: TextIO | None = None) -> logging.Handler:
    """A handler that writes one JSON object per line.

    Also used by `log_config.json`, so uvicorn's reloader process (which never imports the app)
    logs JSON too.
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_FLAG, True)
    return handler


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
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
