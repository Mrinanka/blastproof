"""The MCP server: four thin tools over app/services/agent_calls.py (backend plan §6, step 6).

Built once per `create_app()` (D-096: `session_manager.run()` works once per server). Each tool
takes the agent from the middleware's contextvar, calls its service and returns a Pydantic result,
never `ToolError` (D-100). A failure the service did not handle is logged as a reason code only
and the agent gets the `failed` template, so the SDK never sees, or logs, an exception.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.mcp.auth import NoAgentError, require_agent
from app.schemas.mcp import (
    ActionStatusResult,
    QueryDatabaseResult,
    TableDescription,
    TableList,
)
from app.services import agent_calls, agent_messages
from app.services.agent_calls import INTERNAL_ERROR, CallContext
from app.workflow.runner import WorkflowRunner

# Generous limits checked by the SDK before our code runs; the classifier applies the real 20 KB cap
# (and the workflow records the refusal), these only stop huge values from being stored.
MAX_SQL_CHARS = 100_000
MAX_INTENT_CHARS = 2_000
MAX_NAME_CHARS = 200
MAX_ID_CHARS = 100

INSTRUCTIONS = (
    "Blastproof is the gateway to the company database. Use list_tables and describe_table to "
    "learn the schema, and query_database to run SQL. Say what you are trying to do in `intent`. "
    "Reads return rows. A write is previewed and checked first: it may be held for a person to "
    "approve, in which case call get_action_status with its action_id until it is decided."
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

logger = structlog.get_logger()


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """The DNS-rebinding allowlist (D-099): loopback on any port, plus PUBLIC_BASE_URL's host."""
    hosts = ["localhost:*", "127.0.0.1:*", settings.public_host]
    origins = ["http://localhost:*", "http://127.0.0.1:*", settings.public_origin]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


def build_mcp_server(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    *,
    runner: Callable[[], WorkflowRunner | None] = lambda: None,
    clock: Clock | None = None,
) -> MCPServer:
    mcp = MCPServer(name="blastproof", instructions=INSTRUCTIONS)
    clock = clock or SystemClock()

    async def guarded[R](
        tool: str, call: Callable[[CallContext], Awaitable[R]], failed_result: Callable[[str], R]
    ) -> R:
        try:
            agent = require_agent()
        except NoAgentError:
            logger.error("tool_refused", tool=tool, reason_code="no_agent")
            return failed_result(agent_messages.failed("not_authenticated"))
        try:
            if sessionmaker is None:
                raise RuntimeError("database not configured")
            return await call(CallContext(settings, sessionmaker, agent, clock, runner))
        except Exception as exc:
            # The class name only: the message can hold raw values or a connection string.
            logger.error("tool_failed", tool=tool, reason_code=type(exc).__name__)
            return failed_result(agent_messages.failed(INTERNAL_ERROR))

    @mcp.tool()
    async def query_database(
        sql: Annotated[
            str, Field(description="One SQL statement.", min_length=1, max_length=MAX_SQL_CHARS)
        ],
        intent: Annotated[
            str,
            Field(
                description="What you are trying to do, in plain words.",
                min_length=1,
                max_length=MAX_INTENT_CHARS,
            ),
        ],
        retry_of: Annotated[
            str | None,
            Field(
                description="The action_id of a rejected or blocked query this one replaces.",
                max_length=MAX_ID_CHARS,
            ),
        ] = None,
    ) -> QueryDatabaseResult:
        """Run one SQL statement on the company database through Blastproof.

        A SELECT returns at most 500 rows; add a LIMIT or a narrower WHERE for large tables.
        A write is previewed and checked: it may commit, be blocked, or be held for a person to
        approve. If the result says it is held or still being checked, call get_action_status with
        its action_id until it is decided.
        """
        return await guarded(
            "query_database",
            lambda ctx: agent_calls.query_database(ctx, sql, intent, retry_of),
            lambda message: QueryDatabaseResult(action_id=None, status="failed", message=message),
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_tables() -> TableList:
        """List the company database's tables, each with a one-line description."""
        return await guarded(
            "list_tables",
            agent_calls.list_tables,
            lambda message: TableList(action_id=None, status="failed", message=message),
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def describe_table(
        table: Annotated[
            str,
            Field(description="A name from list_tables.", min_length=1, max_length=MAX_NAME_CHARS),
        ],
    ) -> TableDescription:
        """Show one table's columns, their types, whether they can be null, and any notes."""
        return await guarded(
            "describe_table",
            lambda ctx: agent_calls.describe_table(ctx, table),
            lambda message: TableDescription(
                action_id=None, status="failed", table=table, message=message
            ),
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_action_status(
        action_id: Annotated[
            str, Field(description="The action_id from an earlier call.", max_length=MAX_ID_CHARS)
        ],
        wait_seconds: Annotated[
            int,
            Field(
                description=(
                    "How long to wait for a decision,"
                    f" 0 to {settings.status_max_wait_seconds} seconds."
                ),
                ge=0,
                le=settings.status_max_wait_seconds,
            ),
        ] = 30,
    ) -> ActionStatusResult:
        """Get the status and decision of one of your actions.

        Waits until the action is decided or `wait_seconds` pass, so call it again while it is held.
        """
        return await guarded(
            "get_action_status",
            lambda ctx: agent_calls.action_status(ctx, action_id, wait_seconds),
            lambda message: ActionStatusResult(action_id=action_id, status=None, message=message),
        )

    return mcp
