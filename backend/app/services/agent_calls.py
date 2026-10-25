"""What each MCP tool does, in order: record the action, run it, record how it ended (step 6).

`query_database`, `list_tables` and `describe_table` each become one action under the agent
owner's `user_id`: `received` (committed before anything runs), then `executed` or `failed` with
`total_ms`, each with its audit event. Reads stay outside the LangGraph workflow (ARCH-10).
`get_action_status` asks about an existing action, so it records nothing and is only logged.

Expected failures come back as the agent_messages.py text. Anything unexpected after the action
exists is logged as a reason code only (never the exception text, which can hold raw values),
recorded as `failed: internal_error`, and the agent gets the `failed` template (D-100).
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.auth import CurrentAgent
from app.db import actions as actions_db
from app.db.enums import ActionStatus
from app.db.models import Action
from app.schemas.mcp import (
    ActionStatusResult,
    ColumnSummary,
    QueryDatabaseResult,
    TableDescription,
    TableList,
    TableSummary,
)
from app.services import agent_messages, company_db, reads
from app.services.systems import ensure_main_system

INTERNAL_ERROR = "internal_error"
PARSE_TIMEOUT_SECONDS = 2
# A backstop over the whole company-database part: connect (10 s) plus the 5 s statement timeout.
READ_DEADLINE_SECONDS = 20
# Postgres messages are short; this only stops a pathological one from flooding the reply.
_MAX_QUERY_ERROR_LENGTH = 500

logger = structlog.get_logger()


@dataclass(frozen=True)
class CallContext:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    agent: CurrentAgent


@dataclass(frozen=True)
class _Outcome[R]:
    result: R
    failure_reason: str | None = None  # None means executed
    data: dict[str, Any] = field(default_factory=dict)  # for the final audit event


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _start(
    ctx: CallContext,
    *,
    tool: str,
    intent: str | None = None,
    sql: str | None = None,
    statement_type: str | None = None,
    tables: list[str] | None = None,
) -> uuid.UUID:
    """Record the action as `received` and commit, so it exists even if the process dies next."""
    async with ctx.sessionmaker() as session, session.begin():
        system = await ensure_main_system(session, ctx.settings)
        action = await actions_db.create_action(
            session,
            user_id=ctx.agent.owner_id,
            agent_id=ctx.agent.agent_id,
            system_id=system.id,
            tool=tool,
            intent=intent,
            sql=sql,
            statement_type=statement_type,
            tables=tables,
        )
    structlog.contextvars.bind_contextvars(action_id=str(action.id))
    logger.info("action_received", tool=tool)
    return action.id


async def _finish(
    ctx: CallContext, action_id: uuid.UUID, started: float, outcome: _Outcome[Any]
) -> None:
    reason = outcome.failure_reason
    status = ActionStatus.FAILED if reason else ActionStatus.EXECUTED
    total_ms = _elapsed_ms(started)
    data = {**outcome.data, **({"reason": reason} if reason else {})}
    async with ctx.sessionmaker() as session, session.begin():
        await actions_db.set_status(
            session, action_id, status, failure_reason=reason, total_ms=total_ms, data=data
        )
    logger.info("action_finished", status=status.value, reason_code=reason, total_ms=total_ms)


async def _run_recorded[R](
    ctx: CallContext,
    action_id: uuid.UUID,
    started: float,
    work: Callable[[], Awaitable[_Outcome[R]]],
    failed_result: Callable[[str], R],
) -> R:
    try:
        outcome = await work()
    except Exception as exc:
        # The class name only: the message can hold raw values or a connection string.
        logger.error("tool_failed", reason_code=type(exc).__name__)
        outcome = _Outcome(
            failed_result(agent_messages.failed(INTERNAL_ERROR)), failure_reason=INTERNAL_ERROR
        )
    await _finish(ctx, action_id, started, outcome)
    return outcome.result


def _company_db_failure(exc: Exception) -> tuple[str, str] | None:
    """(failure_reason, message for the agent) for an expected company-database failure."""
    if isinstance(
        exc, company_db.CompanyDbUnavailableError | OSError | TimeoutError | asyncpg.InterfaceError
    ):
        return "company_db_unavailable", agent_messages.failed("company_db_unavailable")
    if isinstance(exc, asyncpg.QueryCanceledError):
        return "statement_timeout", agent_messages.failed("statement_timeout")
    if isinstance(exc, asyncpg.PostgresError):
        sqlstate = exc.sqlstate or "XX000"
        text = (getattr(exc, "message", None) or type(exc).__name__)[:_MAX_QUERY_ERROR_LENGTH]
        # Only the code is logged and stored; the text goes to the agent alone (D-104).
        return f"query_error_{sqlstate}", agent_messages.query_error(text, sqlstate)
    return None


async def _check_read(sql: str) -> reads.ReadCheck:
    """check_read in a worker thread, capped at 2 s (backend plan §12). Fails closed."""
    try:
        check = asyncio.to_thread(reads.check_read, sql)
        return await asyncio.wait_for(check, PARSE_TIMEOUT_SECONDS)
    except TimeoutError:
        return reads.ReadCheck(ok=False, reason="parse_timeout")
    except Exception as exc:
        logger.error("read_check_failed", reason_code=type(exc).__name__)
        return reads.ReadCheck(ok=False, reason="check_error")


async def query_database(ctx: CallContext, sql: str, intent: str) -> QueryDatabaseResult:
    started = time.perf_counter()
    check = await _check_read(sql)
    action_id = await _start(
        ctx,
        tool="query_database",
        intent=intent,
        sql=sql,
        statement_type="select" if check.ok else None,
        tables=check.tables,
    )
    aid = str(action_id)

    def failed_result(message: str) -> QueryDatabaseResult:
        return QueryDatabaseResult(action_id=aid, status="failed", message=message)

    async def work() -> _Outcome[QueryDatabaseResult]:
        if not check.ok:
            logger.info("read_refused", reason_code=check.reason)
            return _Outcome(
                failed_result(agent_messages.not_supported()),
                failure_reason="not_supported",
                data={"check": check.reason},
            )
        try:
            async with (
                asyncio.timeout(READ_DEADLINE_SECONDS),
                company_db.connect_reader(ctx.settings) as conn,
            ):
                read = await reads.run_read(conn, sql)
        except Exception as exc:
            known = _company_db_failure(exc)
            if known is None:
                raise
            return _Outcome(failed_result(known[1]), failure_reason=known[0])
        result = QueryDatabaseResult(
            action_id=aid,
            status="executed",
            columns=read.columns,
            rows=read.rows,
            row_count=len(read.rows),
            truncated=read.truncated,
            message=agent_messages.TRUNCATED if read.truncated else None,
        )
        return _Outcome(result, data={"row_count": len(read.rows), "truncated": read.truncated})

    return await _run_recorded(ctx, action_id, started, work, failed_result)


async def list_tables(ctx: CallContext) -> TableList:
    started = time.perf_counter()
    action_id = await _start(ctx, tool="list_tables")
    aid = str(action_id)

    def failed_result(message: str) -> TableList:
        return TableList(action_id=aid, status="failed", message=message)

    async def work() -> _Outcome[TableList]:
        try:
            async with (
                asyncio.timeout(READ_DEADLINE_SECONDS),
                company_db.connect_reader(ctx.settings) as conn,
            ):
                tables = await reads.list_tables(conn)
        except Exception as exc:
            known = _company_db_failure(exc)
            if known is None:
                raise
            return _Outcome(failed_result(known[1]), failure_reason=known[0])
        summaries = [TableSummary(name=t.name, description=t.description) for t in tables]
        result = TableList(action_id=aid, status="executed", tables=summaries)
        return _Outcome(result, data={"table_count": len(summaries)})

    return await _run_recorded(ctx, action_id, started, work, failed_result)


async def describe_table(ctx: CallContext, table: str) -> TableDescription:
    started = time.perf_counter()
    action_id = await _start(ctx, tool="describe_table", tables=[table])
    aid = str(action_id)

    def failed_result(message: str) -> TableDescription:
        return TableDescription(action_id=aid, status="failed", table=table, message=message)

    async def work() -> _Outcome[TableDescription]:
        try:
            async with (
                asyncio.timeout(READ_DEADLINE_SECONDS),
                company_db.connect_reader(ctx.settings) as conn,
            ):
                found = await reads.describe_table(conn, table)
        except Exception as exc:
            known = _company_db_failure(exc)
            if known is None:
                raise
            return _Outcome(failed_result(known[1]), failure_reason=known[0])
        if found is None:
            return _Outcome(
                failed_result(agent_messages.table_not_found(table)),
                failure_reason="table_not_found",
            )
        info, columns = found
        result = TableDescription(
            action_id=aid,
            status="executed",
            table=info.name,
            description=info.description,
            columns=[
                ColumnSummary(
                    name=c.name, type=c.type, nullable=c.nullable, description=c.description
                )
                for c in columns
            ],
        )
        return _Outcome(result, data={"column_count": len(columns)})

    return await _run_recorded(ctx, action_id, started, work, failed_result)


def _failure_message(action: Action) -> str:
    """The message the agent got when the action failed, rebuilt from what was stored.

    A query error's Postgres text is never stored (D-104), so it comes back as the `failed` code.
    """
    reason = action.failure_reason or INTERNAL_ERROR
    if reason == "not_supported":
        return agent_messages.not_supported()
    if reason == "table_not_found" and action.tables:
        return agent_messages.table_not_found(action.tables[0])
    return agent_messages.failed(reason)


async def action_status(ctx: CallContext, action_id: str) -> ActionStatusResult:
    """An owner-scoped lookup with no waiting yet (D-106). Creates no action."""
    logger.info("tool_called", tool="get_action_status")
    not_found = ActionStatusResult(
        action_id=action_id, status=None, message=agent_messages.not_found(action_id)
    )
    try:
        parsed = uuid.UUID(action_id)
    except ValueError:
        return not_found
    async with ctx.sessionmaker() as session:
        # Admin widening off: an admin's agent sees only its owner's actions (D-055).
        action = await actions_db.get_visible_action(
            session, parsed, ctx.agent.owner_id, is_admin=False
        )
    if action is None:
        return not_found
    message = _failure_message(action) if action.status == ActionStatus.FAILED else None
    return ActionStatusResult(
        action_id=str(action.id), status=action.status, decision=action.decision, message=message
    )
