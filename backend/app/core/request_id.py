"""Request IDs (OBS-04): every response gets an X-Request-ID header, and every log line its ID.

Pure ASGI rather than Starlette's BaseHTTPMiddleware, which breaks streaming responses such as
the MCP transport mounted in step 6.

Unhandled exceptions are turned into the 500 response here, not by Starlette's outer error
handler: that handler runs outside this middleware, so its 500 would have no X-Request-ID and its
traceback log line no request_id. The log line names the exception class and where it was raised,
never its message: a database error can carry row values (SEC-14, ARCH-17).
"""

import time
import traceback
import uuid
from pathlib import Path

import structlog
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Request-ID"
_HEADER_KEY = HEADER.lower().encode("latin-1")
_MAX_INCOMING_LENGTH = 64
MAX_LOGGED_FRAMES = 15

logger = structlog.get_logger()

# Says nothing about the cause: details go to the log line, never to the client.
_INTERNAL_ERROR_BODY = {"error": {"code": "internal_error", "message": "Internal server error"}}


def crash_frames(exc: BaseException) -> list[str]:
    """`file:line function` for the innermost frames, innermost last.

    No message, no source lines, no locals: enough to find the code, nothing from the data.
    """
    frames = [
        f"{Path(frame.f_code.co_filename).name}:{line} {frame.f_code.co_name}"
        for frame, line in traceback.walk_tb(exc.__traceback__)
    ]
    return frames[-MAX_LOGGED_FRAMES:]


def parse_request_id(raw: bytes | None) -> str | None:
    """Return the incoming ID in canonical form if it is a UUID, else None.

    Only UUIDs are trusted, so a client cannot put arbitrary text (newlines, huge values) into logs.
    """
    if raw is None or len(raw) > _MAX_INCOMING_LENGTH:
        return None
    try:
        return str(uuid.UUID(raw.decode("ascii")))
    except (UnicodeDecodeError, ValueError):
        return None


def _logged_path(scope: Scope) -> str:
    """The route's pattern (`/v1/knowledge/{source}`), not the URL: a path parameter can be a file
    name. A request that matched no route is logged as sent."""
    pattern = getattr(scope.get("route"), "path", None)
    return pattern if isinstance(pattern, str) else scope["path"]


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(_HEADER_KEY)
        request_id = parse_request_id(incoming) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status = 500
        response_started = False
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            logger.error(
                "unhandled_exception", exc_type=type(exc).__name__, frames=crash_frames(exc)
            )
            if response_started:
                # Too late for a clean 500; the server closes the connection.
                raise
            await JSONResponse(_INTERNAL_ERROR_BODY, status_code=500)(
                scope, receive, send_with_request_id
            )
        finally:
            logger.info(
                "request_finished",
                method=scope["method"],
                path=_logged_path(scope),
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()
