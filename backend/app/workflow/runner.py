"""The workflow runner (backend plan step 15): starts write runs, claims a reviewer's decision,
expires reviews, and recovers after a restart.

Rules (CLAUDE.md, architecture rules):

* Fail closed. A run that crashes outside the graph ends as `failed`. A `committing` action is never
  failed here, because a commit may or may not have reached the company database: when its thread
  is paused before `commit`, the graph is continued and the committer settles it from the marker
  row; with no such thread it is left alone and logged.
* A decision is claimed with one conditional UPDATE before the graph is resumed (D-177): of two
  callers at once, exactly one wins. The graph never decides who may approve.
* Every background task is held in `WorkflowRunner._tasks` until it is done, so it cannot be garbage
  collected mid-run, and `drain()` can wait for it.
* Recovery and the sweep run under one advisory lock, so two backend processes never do either at
  the same time.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import structlog
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.clock import Clock
from app.db.actions import (
    IllegalTransitionError,
    StatusView,
    add_event,
    claim_decision,
    expire_if_overdue,
    get_visible_action,
    overdue_reviews,
    set_status,
    status_view,
    stuck_actions,
)
from app.db.enums import ActionStatus, AuditEventType, Decision
from app.db.models import Action
from app.db.retention import delete_older_than
from app.workflow.graph import (
    REVIEW_NODE,
    NotAwaitingReviewError,
    resume_write,
    run_config,
    run_write,
)

logger = structlog.get_logger()

RUNNER_LOCK_KEY = 0x424C5752  # "BLWR"; distinct from neon.SWEEP_LOCK_KEY
POLL_SECONDS = 0.5
SWEEP_EVERY_SECONDS = 600
RECOVERY_RETRY_SECONDS = 10
STUCK_AFTER = timedelta(minutes=5)
DRAIN_SECONDS = 45

_S = ActionStatus
# Statuses with a node running in this process: after a restart nothing is running them.
IN_FLIGHT = frozenset({_S.RECEIVED, _S.CLASSIFIED, _S.PREVIEWING, _S.DRIFTED})
# What `query_database` waits for: a settled answer, or a review waiting for a person.
PENDING_OK = frozenset(
    {_S.AWAITING_REVIEW, _S.COMMITTED, _S.BLOCKED, _S.REJECTED, _S.EXPIRED, _S.FAILED}
)
# What `get_action_status` long-polls for.
DECIDED = frozenset({_S.COMMITTED, _S.BLOCKED, _S.REJECTED, _S.EXPIRED, _S.FAILED})
# A crash outside the graph may fail these; none has started a commit.
CRASH_FAILS = frozenset({_S.RECEIVED, _S.CLASSIFIED, _S.PREVIEWING, _S.APPROVED})
# The sweep fails these when they sit in the status too long.
STUCK_STATUSES = IN_FLIGHT


class WorkflowUnavailableError(RuntimeError):
    """The runner takes no new writes or decisions right now (step 17 tells the agent: busy)."""

    def __init__(self, reason: Literal["recovering", "draining"]) -> None:
        super().__init__(reason)
        self.reason = reason


class ActionNotFoundError(LookupError):
    """An unknown action or someone else's: a 404, never a 403."""


class AlreadyDecidedError(RuntimeError):
    """The review was decided, expired or is not open (a 409). `decided_by_name` is who decided."""

    def __init__(self, status: str, decided_by_name: str | None) -> None:
        super().__init__(status)
        self.status = status
        self.decided_by_name = decided_by_name


@dataclass(frozen=True)
class Decider:
    user_id: uuid.UUID
    display_name: str
    is_admin: bool


@dataclass(frozen=True)
class SweepReport:
    expired: int
    failed_stuck: int
    deleted: dict[str, int]
    reconciled: int = 0


@dataclass(frozen=True)
class RecoveryReport:
    failed: int
    resumed: int
    continued: int
    untouched_committing: int
    reconciled: int = 0


class WorkflowRunner:
    def __init__(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._graph = graph
        self._sessionmaker = sessionmaker
        self._clock = clock
        self._settings = settings
        self._sleep = sleep
        self._monotonic = monotonic
        self._tasks: set[asyncio.Task[None]] = set()
        # Actions whose graph work runs in this process; the sweep never touches those.
        self._running: set[uuid.UUID] = set()
        self._state: Literal["recovering", "ready", "draining"] = "recovering"

    @property
    def accepting(self) -> bool:
        """False while recovering or draining: no new write or decision races recovery."""
        return self._state == "ready"

    def _require_accepting(self) -> None:
        if self._state != "ready":
            raise WorkflowUnavailableError(self._state)

    def mark_recovered(self) -> None:
        if self._state == "recovering":
            self._state = "ready"

    # -- tasks --

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)  # the set keeps it alive; the callback lets it go when done
        task.add_done_callback(self._tasks.discard)

    async def join(self) -> None:
        """Wait until every background task is done (tests; `drain` for shutdown)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _fail_if(
        self, action_id: uuid.UUID, statuses: frozenset[ActionStatus], reason: str
    ) -> None:
        """Best effort: move an action in one of `statuses` to `failed`. Never raises."""
        try:
            async with self._sessionmaker.begin() as session:
                action = await session.get(Action, action_id, with_for_update=True)
                if action is not None and ActionStatus(action.status) in statuses:
                    await set_status(
                        session,
                        action_id,
                        ActionStatus.FAILED,
                        failure_reason=reason,
                        decision=Decision.BLOCK,
                        data={"reason": reason},
                    )
        except Exception as exc:
            logger.error(
                "fail_action_failed", action_id=str(action_id), error_class=type(exc).__name__
            )

    async def _guard(self, action_id: uuid.UUID, work: Awaitable[Any]) -> None:
        """Run graph work; an exception that escaped the graph fails the action (never a commit)."""
        self._running.add(action_id)
        try:
            await work
        except asyncio.CancelledError:
            raise  # shutdown: the action stays mid-flow for recovery
        except Exception as exc:
            # The class only: an exception message can hold SQL, row values or a connection string.
            logger.error(
                "workflow_crashed", action_id=str(action_id), error_class=type(exc).__name__
            )
            await self._fail_if(action_id, CRASH_FAILS, "workflow_error")
        finally:
            self._running.discard(action_id)

    # -- starting a write --

    def start(self, action_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        """Run a write in the background. Raises `WorkflowUnavailableError` unless accepting."""
        self._require_accepting()
        self._spawn(
            self._guard(
                action_id,
                run_write(
                    self._graph,
                    action_id=action_id,
                    agent_id=agent_id,
                    env=self._settings.app_env,
                ),
            )
        )

    # -- waiting --

    async def wait_for_decision(
        self,
        action_id: uuid.UUID,
        owner_id: uuid.UUID,
        timeout: float,  # noqa: ASYNC109 - a poll budget, not an asyncio timeout
        *,
        until: frozenset[ActionStatus] = DECIDED,
    ) -> StatusView | None:
        """Poll until the action's status is in `until`, or `timeout` seconds pass (then the latest
        view). None for an unknown or someone else's action (read through `visible_actions`)."""
        deadline = self._monotonic() + timeout
        while True:
            async with self._sessionmaker() as session:
                view = await status_view(session, action_id, owner_id, is_admin=False)
            if view is None or ActionStatus(view.status) in until:
                return view
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return view
            await self._sleep(min(POLL_SECONDS, remaining))

    # -- a reviewer's decision --

    async def resume(
        self, action_id: uuid.UUID, *, approved: bool, note: str | None, decider: Decider
    ) -> StatusView:
        """Claim the decision, then resume the paused graph in the background.

        Raises `ActionNotFoundError` (unknown or not visible), `AlreadyDecidedError` (decided,
        expired or not awaiting review) or `WorkflowUnavailableError`.
        """
        self._require_accepting()
        now = self._clock.now()
        decided: AlreadyDecidedError | None = None
        view: StatusView | None = None
        agent_id: uuid.UUID | None = None
        async with self._sessionmaker.begin() as session:
            action = await get_visible_action(
                session, action_id, decider.user_id, is_admin=decider.is_admin
            )
            if action is None:
                raise ActionNotFoundError
            agent_id = action.agent_id
            won = await claim_decision(
                session,
                action_id,
                user_id=decider.user_id,
                is_admin=decider.is_admin,
                approved=approved,
                note=note,
                now=now,
            )
            if won:
                status = ActionStatus.APPROVED if approved else ActionStatus.REJECTED
                # The same transaction: the claim and its event are saved together or not at all.
                await add_event(
                    session,
                    action_id,
                    AuditEventType(status.value),
                    {"user_id": str(decider.user_id), "display_name": decider.display_name},
                )
                view = await status_view(
                    session, action_id, decider.user_id, is_admin=decider.is_admin
                )
            else:
                # Not claimed: past its deadline but not yet swept? Expire it now (same rule as the
                # sweep), so a review is never approved after `expires_at`.
                await expire_if_overdue(session, action_id, now)
                current = await status_view(
                    session, action_id, decider.user_id, is_admin=decider.is_admin
                )
                assert current is not None
                decided = AlreadyDecidedError(current.status, current.decided_by_name)
        if decided is not None:
            raise decided
        assert view is not None and agent_id is not None
        self._spawn(self._resume_graph(action_id, agent_id, approved))
        return view

    async def _resume_graph(
        self, action_id: uuid.UUID, agent_id: uuid.UUID, approved: bool
    ) -> None:
        async def work() -> None:
            try:
                await resume_write(
                    self._graph,
                    action_id=action_id,
                    agent_id=agent_id,
                    env=self._settings.app_env,
                    approved=approved,
                )
            except NotAwaitingReviewError:
                logger.error("resume_mismatch", action_id=str(action_id), approved=approved)
                if approved:  # a rejected action has nothing to commit: only the log
                    await self._fail_if(action_id, frozenset({_S.APPROVED}), "resume_mismatch")

        await self._guard(action_id, work())

    # -- recovery --

    async def _lock(self, session: AsyncSession) -> bool:
        got = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RUNNER_LOCK_KEY}
        )
        return bool(got.scalar_one())

    async def recover(self) -> RecoveryReport | None:
        """Settle what a restart left behind, then start accepting. None when another process holds
        the lock (this one then stays not accepting and `maintain` tries again)."""
        failed = resumed = continued = untouched = reconciled = 0
        launches: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        async with self._sessionmaker.begin() as session:
            if not await self._lock(session):
                return None
            rows = (
                await session.execute(
                    select(Action.id, Action.agent_id, Action.status).where(
                        Action.status.in_(
                            [
                                status.value
                                for status in IN_FLIGHT
                                | {_S.AWAITING_REVIEW, _S.APPROVED, _S.REJECTED, _S.COMMITTING}
                            ]
                        )
                    )
                )
            ).all()
            for action_id, agent_id, raw_status in rows:
                status = ActionStatus(raw_status)
                with structlog.contextvars.bound_contextvars(action_id=str(action_id)):
                    if status == _S.COMMITTING:
                        # A commit may or may not have reached the company database. Continue the
                        # graph at `commit`: the committer reads the marker and settles it.
                        if await self._paused_before_commit(action_id, agent_id):
                            launches.append((action_id, agent_id, "continue"))
                            reconciled += 1
                        else:
                            logger.warning("needs_reconcile")
                            untouched += 1
                        continue
                    if status in IN_FLIGHT:
                        await self._fail_restart(session, action_id)
                        failed += 1
                        continue
                    snapshot = await self._graph.aget_state(
                        run_config(action_id, agent_id, self._settings.app_env)
                    )
                    next_nodes = tuple(snapshot.next)
                    if status == _S.AWAITING_REVIEW:
                        # Not paused at the review: it could be claimed and then never resumed.
                        if next_nodes != (REVIEW_NODE,):
                            await self._fail_restart(session, action_id)
                            failed += 1
                    elif next_nodes == (REVIEW_NODE,):
                        launches.append((action_id, agent_id, f"resume:{status.value}"))
                        resumed += 1
                    elif next_nodes:
                        launches.append((action_id, agent_id, "continue"))
                        continued += 1
                    elif status == _S.APPROVED:
                        await self._fail_restart(session, action_id)
                        failed += 1
                    else:  # rejected is terminal: nothing can commit, so leave it
                        logger.warning("rejected_without_thread")
        # After the commit: a launched run must see the statuses recovery just wrote.
        for action_id, agent_id, how in launches:
            self._spawn(self._relaunch(action_id, agent_id, how))
        self.mark_recovered()
        report = RecoveryReport(failed, resumed, continued, untouched, reconciled)
        logger.info("recovery_done", **report.__dict__)
        return report

    async def _paused_before_commit(self, action_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        snapshot = await self._graph.aget_state(
            run_config(action_id, agent_id, self._settings.app_env)
        )
        return tuple(snapshot.next) == ("commit",)

    @staticmethod
    async def _fail_restart(session: AsyncSession, action_id: uuid.UUID) -> None:
        await set_status(
            session,
            action_id,
            ActionStatus.FAILED,
            failure_reason="restart",
            decision=Decision.BLOCK,
            data={"reason": "restart"},
        )

    async def _relaunch(self, action_id: uuid.UUID, agent_id: uuid.UUID, how: str) -> None:
        if how.startswith("resume:"):
            await self._resume_graph(action_id, agent_id, how == "resume:approved")
            return
        config = run_config(action_id, agent_id, self._settings.app_env)
        await self._guard(
            action_id, self._graph.ainvoke(None, config, durability="sync", version="v2")
        )

    # -- the sweep --

    async def sweep(self) -> SweepReport | None:
        """Expire overdue reviews, fail stuck actions, delete old working data. None when another
        process holds the lock."""
        now = self._clock.now()
        expired = failed_stuck = 0
        launches: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with self._sessionmaker.begin() as session:
            if not await self._lock(session):
                return None
            for action_id in await overdue_reviews(session, now):
                expired += await expire_if_overdue(session, action_id, now)
            for action_id in await stuck_actions(session, STUCK_STATUSES, now - STUCK_AFTER):
                try:
                    async with session.begin_nested():
                        await set_status(
                            session,
                            action_id,
                            ActionStatus.FAILED,
                            failure_reason="stuck",
                            decision=Decision.BLOCK,
                            data={"reason": "stuck"},
                        )
                    failed_stuck += 1
                except IllegalTransitionError:
                    continue
            # A commit nobody is running any more is reconciled, never failed: it may have happened.
            for action_id in await stuck_actions(
                session, frozenset({_S.COMMITTING}), now - STUCK_AFTER
            ):
                if action_id in self._running:
                    continue
                action = await session.get(Action, action_id)
                if action is not None and await self._paused_before_commit(
                    action_id, action.agent_id
                ):
                    launches.append((action_id, action.agent_id))
                else:
                    logger.warning("needs_reconcile", action_id=str(action_id))
            deleted = await delete_older_than(
                session, now - timedelta(days=self._settings.retention_days)
            )
        for action_id, agent_id in launches:
            self._spawn(self._relaunch(action_id, agent_id, "continue"))
        report = SweepReport(expired, failed_stuck, deleted, len(launches))
        logger.info(
            "sweep_done",
            expired=expired,
            failed_stuck=failed_stuck,
            deleted=deleted,
            reconciled=len(launches),
        )
        return report

    async def maintain(self) -> None:
        """Recover (retrying until it succeeds), then sweep every 10 minutes. Never stops on an
        error: each one is logged by class."""
        while self._state == "recovering":
            try:
                if await self.recover() is not None:
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("recovery_failed", error_class=type(exc).__name__)
            await self._sleep(RECOVERY_RETRY_SECONDS)
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("sweep_failed", error_class=type(exc).__name__)
            await self._sleep(SWEEP_EVERY_SECONDS)

    # -- shutdown --

    async def drain(self, timeout: float = DRAIN_SECONDS) -> None:  # noqa: ASYNC109
        """Stop taking work, give running tasks `timeout` seconds, then cancel the rest. A cancelled
        action stays mid-flow (never marked failed): the next start's recovery settles it."""
        self._state = "draining"
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        if pending:
            logger.warning("drain_cancelled", tasks=len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info("drain_done")
