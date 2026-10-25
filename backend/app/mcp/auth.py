"""Agent-key check for `/mcp` (D-098, SEC-33).

Pure ASGI, like RequestIdMiddleware: FastAPI dependencies do not run for the SDK's route, and
BaseHTTPMiddleware breaks streaming responses. On `/mcp` exactly, it checks the bearer key with
`authenticate_agent_key` and answers 401 `invalid_agent_key` (as `get_current_agent` does) or 503
`unavailable` when the database cannot be asked, so a failed check never lets a call through.

On success the agent goes into a contextvar and `scope["state"]`. Tools call `require_agent()`,
which refuses the call if the contextvar is unset (fail closed if the SDK ever runs a handler
outside the request's context).
"""

import contextvars
import json

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.auth import INVALID_AGENT_KEY_MESSAGE, CurrentAgent, authenticate_agent_key

MCP_PATH = "/mcp"

current_agent: contextvars.ContextVar[CurrentAgent | None] = contextvars.ContextVar(
    "current_agent", default=None
)

logger = structlog.get_logger()


class NoAgentError(RuntimeError):
    """A tool ran without an authenticated agent in its context."""


def require_agent() -> CurrentAgent:
    agent = current_agent.get()
    if agent is None:
        raise NoAgentError("no authenticated agent")
    return agent


def _bearer_key(headers: Headers) -> str | None:
    scheme, _, key = headers.get("authorization", "").partition(" ")
    return (key.strip() or None) if scheme.lower() == "bearer" else None


async def _send_error(
    send: Send, status: int, code: str, message: str, extra: list[tuple[bytes, bytes]]
) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class AgentAuthMiddleware:
    def __init__(self, app: ASGIApp, sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
        self.app = app
        self.sessionmaker = sessionmaker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != MCP_PATH:
            await self.app(scope, receive, send)
            return

        if self.sessionmaker is None:
            logger.error("database_not_configured")
            await _send_error(send, 503, "unavailable", "Blastproof's database is unavailable.", [])
            return
        try:
            async with self.sessionmaker() as session:
                agent = await authenticate_agent_key(session, _bearer_key(Headers(scope=scope)))
        except Exception as exc:
            # The class name only: a connection error's text can hold the database URL.
            logger.error("agent_auth_unavailable", reason_code=type(exc).__name__)
            await _send_error(send, 503, "unavailable", "Blastproof's database is unavailable.", [])
            return
        if agent is None:
            await _send_error(
                send,
                401,
                "invalid_agent_key",
                INVALID_AGENT_KEY_MESSAGE,
                [(b"www-authenticate", b"Bearer")],
            )
            return

        scope.setdefault("state", {})["agent"] = agent
        token = current_agent.set(agent)
        try:
            await self.app(scope, receive, send)
        finally:
            current_agent.reset(token)
