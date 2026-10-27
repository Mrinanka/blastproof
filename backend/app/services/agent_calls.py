"""What each MCP tool does, in order: record the action, run it, record how it ended (step 6).

`list_tables`, `describe_table` and a read through `query_database` each become one action under
the agent owner's `user_id`: `received` (committed before anything runs), then `executed` or
`failed` with `total_ms`, each with its audit event. Reads stay outside the LangGraph workflow
(ARCH-10). A write through `query_database` is recorded as `received`, handed to the workflow runner
and waited for up to `REVIEW_WAIT_SECONDS` (step 17); the graph records the rest. Two calls record
nothing: a write over the per-agent rate limit, and a write while the runner takes none (BE-21's
exceptions). `get_action_status` asks about an existing action, so it records nothing and is only
logged.

Expected failures come back as the agent_messages.py text. Anything unexpected after the action
exists is logged as a reason code only (never the exception text, which can hold raw values),
recorded as `failed: internal_error`, and the agent gets the `failed` template (D-100).
"""

import asyncio
import math
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
from app.core.clock import Clock
from app.db import actions as actions_db
from app.db import knowledge as knowledge_db
from app.db.enums import ActionStatus, Decision
from app.schemas.mcp import (
    ActionStatusResult,
    ColumnSummary,
    KnowledgeNote,
    QueryDatabaseResult,
    TableDescription,
    TableList,
    TableSummary,
)
from app.services import agent_messages, agent_outcomes, company_db, reads
from app.services.agent_outcomes import BUSY_RETRY_SECONDS, INTERNAL_ERROR
from app.services.classifier import Classification, Kind, classify_in_worker
from app.services.knowledge import SCHEMA_SCAN_SOURCE
from app.services.systems import ensure_main_system
from app.workflow.runner import (
    DECIDED,
    PENDING_OK,
    WorkflowRunner,
    WorkflowUnavailableError,
)

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
    clock: Clock
    # The runner is built in the lifespan, after the MCP server, so tools look it up per call.
    runner: Callable[[], WorkflowRunner | None]


@dataclass(frozen=True)
class _Outcome[R]:
    result: R
    failure_reason: str | None = None  # None means executed
    data: dict[str, Any] = field(default_factory=dict)  # for the final audit event


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class _RateLimited:
    seconds: int


async def _start(
    ctx: CallContext,
    *,
    tool: str,
    intent: str | None = None,
    sql: str | None = None,
    classification: Classification | None = None,
    tables: list[str] | None = None,
    retry_of: uuid.UUID | None = None,
    kind: str | None = None,
    rate_limit: bool = False,
) -> uuid.UUID | _RateLimited:
    """Record the action as `received` and commit, so it exists even if the process dies next.

    With `rate_limit` (a write), the agent's last 60 seconds are counted first under a per-agent
    lock; at the cap nothing is recorded and the answer is `_RateLimited`.
    """
    async with ctx.sessionmaker() as session, session.begin():
        system = await ensure_main_system(session, ctx.settings)
        if rate_limit:
            await actions_db.lock_agent_writes(session, ctx.agent.agent_id)
            count, oldest_age = await actions_db.recent_writes(session, ctx.agent.agent_id)
            if count >= ctx.settings.max_writes_per_minute_per_agent:
                wait = actions_db.RATE_WINDOW_SECONDS - (oldest_age or 0)
                return _RateLimited(max(1, math.ceil(wait)))
        action = await actions_db.create_action(
            session,
            user_id=ctx.agent.owner_id,
            agent_id=ctx.agent.agent_id,
            system_id=system.id,
            tool=tool,
            intent=intent,
            sql=sql,
            statement_type=classification.statement_type if classification else None,
            tables=list(classification.tables) if classification else tables,
            pinned_sql=classification.pinned_sql if classification else None,
            sql_display=classification.sql_display if classification else None,
            pinned_values=classification.pinned_values if classification else None,
            retry_of=retry_of,
            received_data={"kind": kind} if kind else None,
        )
    structlog.contextvars.bind_contextvars(action_id=str(action.id))
    logger.info("action_received", tool=tool)
    return action.id


async def _started(ctx: CallContext, **fields: Any) -> uuid.UUID:
    """`_start` for a call with no rate limit: always an action ID."""
    action_id = await _start(ctx, **fields)
    assert isinstance(action_id, uuid.UUID)
    return action_id


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


def _not_found_result(retry_of: str) -> QueryDatabaseResult:
    return QueryDatabaseResult(
        action_id=None, status=None, message=agent_messages.not_found(retry_of)
    )


async def _resolve_retry_of(ctx: CallContext, retry_of: str | None) -> uuid.UUID | None | bool:
    """The action this one replaces; False for an unknown, malformed or someone else's ID (SEC-34).

    Looked up through `visible_actions` with admin widening off, like `get_action_status`.
    """
    if retry_of is None:
        return None
    try:
        parsed = uuid.UUID(retry_of)
    except ValueError:
        return False
    async with ctx.sessionmaker() as session:
        found = await actions_db.get_visible_action(
            session, parsed, ctx.agent.owner_id, is_admin=False
        )
    return found.id if found is not None else False


async def query_database(
    ctx: CallContext, sql: str, intent: str, retry_of: str | None = None
) -> QueryDatabaseResult:
    classification, duration_ms = await classify_in_worker(sql, ctx.clock)
    replaced = await _resolve_retry_of(ctx, retry_of)
    if replaced is False:
        logger.info("retry_of_not_found")
        return _not_found_result(retry_of or "")
    assert replaced is None or isinstance(replaced, uuid.UUID)
    if classification.kind == Kind.READ:
        return await _read(ctx, sql, intent, replaced, classification, duration_ms)
    # A write, DDL or anything the classifier could not place: the workflow decides (pass 1 blocks
    # DDL and unknown statements), so no statement is judged here.
    return await _write(ctx, sql, intent, replaced, classification, duration_ms)


async def _read(
    ctx: CallContext,
    sql: str,
    intent: str,
    retry_of: uuid.UUID | None,
    classification: Classification,
    duration_ms: int,
) -> QueryDatabaseResult:
    started = time.perf_counter()
    action_id = await _started(
        ctx,
        tool="query_database",
        intent=intent,
        sql=sql,
        classification=classification,
        retry_of=retry_of,
        kind="read",
    )
    logger.info("classified", kind=classification.kind.value, duration_ms=duration_ms)
    aid = str(action_id)

    def failed_result(message: str) -> QueryDatabaseResult:
        return QueryDatabaseResult(action_id=aid, status="failed", message=message)

    async def work() -> _Outcome[QueryDatabaseResult]:
        try:
            async with (
                asyncio.timeout(READ_DEADLINE_SECONDS),
                company_db.connect_reader(ctx.settings) as conn,
            ):
                # The pinned statement: the record shows exactly what ran.
                read = await reads.run_read(conn, classification.pinned_sql)
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


async def _fail_received(ctx: CallContext, action_id: uuid.UUID, reason: str) -> None:
    """The run never started: `received -> failed`. Best effort; the stuck sweep is the backstop."""
    try:
        async with ctx.sessionmaker() as session, session.begin():
            await actions_db.set_status(
                session,
                action_id,
                ActionStatus.FAILED,
                failure_reason=reason,
                decision=Decision.BLOCK,
                data={"reason": reason},
            )
    except Exception as exc:
        logger.error("fail_action_failed", reason_code=type(exc).__name__)


def _busy_result(seconds: int) -> QueryDatabaseResult:
    return QueryDatabaseResult(action_id=None, status=None, message=agent_messages.busy(seconds))


async def _answer(
    ctx: CallContext, action_id: uuid.UUID, *, polling: bool
) -> tuple[agent_outcomes.AgentView, agent_outcomes.Outcome] | None:
    async with ctx.sessionmaker() as session:
        view = await actions_db.agent_view(session, action_id, ctx.agent.owner_id)
    if view is None:
        return None
    outcome = agent_outcomes.outcome_for(
        view, now=ctx.clock.now(), settings=ctx.settings, polling=polling
    )
    return view, outcome


async def _write(
    ctx: CallContext,
    sql: str,
    intent: str,
    retry_of: uuid.UUID | None,
    classification: Classification,
    duration_ms: int,
) -> QueryDatabaseResult:
    runner = ctx.runner()
    if runner is None:
        # Nothing is recorded: a write that cannot be started must not look like it was tried.
        logger.error("tool_refused", tool="query_database", reason_code="workflow_not_configured")
        return QueryDatabaseResult(
            action_id=None,
            status="failed",
            message=agent_messages.failed("workflow_not_configured"),
        )
    if not runner.accepting:  # recovering or draining: no action, so the stuck sweep never sees it
        logger.warning("write_refused", reason_code="workflow_not_accepting")
        return _busy_result(BUSY_RETRY_SECONDS)
    started_id = await _start(
        ctx,
        tool="query_database",
        intent=intent,
        sql=sql,
        retry_of=retry_of,
        kind="write",
        rate_limit=True,
    )
    if isinstance(started_id, _RateLimited):
        # No SQL, intent or action in the log: nothing was recorded (BE-21's exception).
        logger.warning(
            "rate_limited", agent_id=str(ctx.agent.agent_id), retry_after=started_id.seconds
        )
        return _busy_result(started_id.seconds)
    action_id = started_id
    aid = str(action_id)
    logger.info("classified", kind=classification.kind.value, duration_ms=duration_ms)
    try:
        runner.start(action_id, ctx.agent.agent_id)
    except WorkflowUnavailableError:  # drain began between the check and here
        await _fail_received(ctx, action_id, "workflow_unavailable")
        return QueryDatabaseResult(
            action_id=aid, status="failed", message=agent_messages.busy(BUSY_RETRY_SECONDS)
        )
    try:
        await runner.wait_for_decision(
            action_id, ctx.agent.owner_id, ctx.settings.review_wait_seconds, until=PENDING_OK
        )
        answer = await _answer(ctx, action_id, polling=False)
    except Exception as exc:
        # The run is going on without us: say so, and let the agent poll for the outcome.
        logger.error("write_wait_failed", reason_code=type(exc).__name__)
        return QueryDatabaseResult(
            action_id=aid, status=None, message=agent_messages.in_progress(aid)
        )
    if answer is None:
        return QueryDatabaseResult(
            action_id=aid, status=None, message=agent_messages.failed(INTERNAL_ERROR)
        )
    view, outcome = answer
    return QueryDatabaseResult(
        action_id=aid, status=outcome.status, decision=view.decision, message=outcome.message
    )


async def list_tables(ctx: CallContext) -> TableList:
    started = time.perf_counter()
    action_id = await _started(ctx, tool="list_tables")
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


async def _notes_for(ctx: CallContext, table: str) -> list[KnowledgeNote]:
    """Docs and uploads tagged with this table. The columns are already in the reply, so the schema
    scan is left out. Notes are extra: if they cannot be read, the columns are still returned."""
    try:
        async with ctx.sessionmaker() as session:
            chunks = await knowledge_db.notes_for_table(
                session, table.lower(), exclude_source=SCHEMA_SCAN_SOURCE
            )
    except Exception as exc:
        logger.error("notes_failed", reason_code=type(exc).__name__)
        return []
    return [KnowledgeNote(source=c.source, text=c.content) for c in chunks]


async def describe_table(ctx: CallContext, table: str) -> TableDescription:
    started = time.perf_counter()
    action_id = await _started(ctx, tool="describe_table", tables=[table])
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
        notes = await _notes_for(ctx, info.name)
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
            notes=notes,
        )
        return _Outcome(result, data={"column_count": len(columns), "note_count": len(notes)})

    return await _run_recorded(ctx, action_id, started, work, failed_result)


async def action_status(
    ctx: CallContext, action_id: str, wait_seconds: int = 0
) -> ActionStatusResult:
    """An owner-scoped long poll (backend plan §6). Creates no action."""
    logger.info("tool_called", tool="get_action_status")
    not_found = ActionStatusResult(
        action_id=action_id, status=None, message=agent_messages.not_found(action_id)
    )
    try:
        parsed = uuid.UUID(action_id)
    except ValueError:
        return not_found
    answer = await _answer(ctx, parsed, polling=True)
    if answer is None:
        return not_found
    view, outcome = answer
    runner = ctx.runner()
    status = ActionStatus(view.status)
    waiting = status == ActionStatus.AWAITING_REVIEW
    over = agent_outcomes.over_budget(view, now=ctx.clock.now(), settings=ctx.settings)
    if wait_seconds > 0 and runner is not None and not over:
        # A review waits for a decision. Anything else waits to settle or to reach a review, and
        # `executed` is final too (it is in neither set).
        until = DECIDED if waiting else PENDING_OK
        await runner.wait_for_decision(
            parsed, ctx.agent.owner_id, wait_seconds, until=until | {ActionStatus.EXECUTED}
        )
        answer = await _answer(ctx, parsed, polling=True)
        if answer is None:
            return not_found
        view, outcome = answer
    return ActionStatusResult(
        action_id=str(view.id), status=view.status, decision=view.decision, message=outcome.message
    )
