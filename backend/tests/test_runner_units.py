"""The runner without a database: the task set, `start`'s refusal, the crash log, the poll loop and
the drain. A fake graph and a fake session factory stand in for LangGraph and Postgres."""

import asyncio
import gc
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog

from app.config import Settings
from app.core.clock import FrozenClock
from app.db.actions import StatusView
from app.db.enums import ActionStatus
from app.workflow import runner as runner_module
from app.workflow.runner import (
    DECIDED,
    PENDING_OK,
    POLL_SECONDS,
    WorkflowRunner,
    WorkflowUnavailableError,
)

ACTION = uuid.uuid4()
AGENT = uuid.uuid4()
NOW = datetime(2026, 10, 1, tzinfo=UTC)


class FakeGraph:
    def __init__(self, *, raises: BaseException | None = None, gate: asyncio.Event | None = None):
        self.raises = raises
        self.gate = gate
        self.started = asyncio.Event()
        self.finished = 0

    async def ainvoke(self, *args: Any, **kwargs: Any) -> None:
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.raises is not None:
            raise self.raises
        self.finished += 1


class FakeSessionmaker:
    """`sessionmaker()` and `sessionmaker.begin()` both give a session that does nothing."""

    def __call__(self) -> Any:
        return self._ctx()

    def begin(self) -> Any:
        return self._ctx()

    @asynccontextmanager
    async def _ctx(self) -> AsyncIterator[object]:
        yield object()


def make_runner(graph: Any, *, recovered: bool = True, **kwargs: Any) -> WorkflowRunner:
    runner = WorkflowRunner(
        graph,
        FakeSessionmaker(),  # type: ignore[arg-type]
        FrozenClock(NOW),
        Settings(_env_file=None, app_env="test"),
        **kwargs,
    )
    if recovered:
        runner.mark_recovered()
    return runner


def view(status: str) -> StatusView:
    return StatusView(ACTION, status, None, None, None, None, None, NOW, None)


# -- start -----------------------------------------------------------------------------------


async def test_the_task_is_held_while_running_and_gone_after() -> None:
    gate = asyncio.Event()
    graph = FakeGraph(gate=gate)
    runner = make_runner(graph)
    runner.start(ACTION, AGENT)
    await graph.started.wait()
    gc.collect()  # a task nobody holds can be collected mid-run
    assert len(runner._tasks) == 1
    gate.set()
    await runner.join()
    assert runner._tasks == set() and graph.finished == 1


async def test_start_is_refused_while_not_accepting() -> None:
    graph = FakeGraph()
    runner = make_runner(graph, recovered=False)
    with pytest.raises(WorkflowUnavailableError) as raised:
        runner.start(ACTION, AGENT)
    assert raised.value.reason == "recovering"
    assert not graph.started.is_set() and runner._tasks == set()


async def test_an_escaped_exception_is_logged_by_class_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed: list[tuple[uuid.UUID, str]] = []

    async def fail_if(
        self: WorkflowRunner, action_id: uuid.UUID, statuses: Any, reason: str
    ) -> None:
        assert ActionStatus.COMMITTING not in statuses  # a commit may have started: never touched
        failed.append((action_id, reason))

    monkeypatch.setattr(WorkflowRunner, "_fail_if", fail_if)
    runner = make_runner(FakeGraph(raises=RuntimeError("planted.secret@example.com")))
    runner_module.logger.__dict__.pop("bind", None)  # see test_workflow_fail_closed.py
    with structlog.testing.capture_logs() as logs:
        runner.start(ACTION, AGENT)
        await runner.join()
    assert failed == [(ACTION, "workflow_error")]
    crashed = [entry for entry in logs if entry["event"] == "workflow_crashed"]
    assert len(crashed) == 1 and crashed[0]["error_class"] == "RuntimeError"
    assert "planted.secret" not in repr(logs)


def test_crash_failing_never_covers_a_committing_action() -> None:
    assert ActionStatus.COMMITTING not in runner_module.CRASH_FAILS


# -- wait_for_decision -----------------------------------------------------------------------


class Clockwork:
    """A fake monotonic clock and sleep: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def waiting_runner(
    monkeypatch: pytest.MonkeyPatch, statuses: list[str | None]
) -> tuple[WorkflowRunner, Clockwork, list[int]]:
    clock = Clockwork()
    calls = [0]

    async def fake_status_view(*args: Any, **kwargs: Any) -> StatusView | None:
        status = statuses[min(calls[0], len(statuses) - 1)]
        calls[0] += 1
        return None if status is None else view(status)

    monkeypatch.setattr(runner_module, "status_view", fake_status_view)
    runner = make_runner(FakeGraph(), sleep=clock.sleep, monotonic=clock.monotonic)
    return runner, clock, calls


async def test_it_polls_every_half_second_until_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, clock, calls = waiting_runner(monkeypatch, ["previewing"] * 3 + ["committed"])
    got = await runner.wait_for_decision(ACTION, AGENT, 50)
    assert got is not None and got.status == "committed"
    assert clock.sleeps == [POLL_SECONDS] * 3 and calls[0] == 4


async def test_it_returns_at_once_on_a_settled_status(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, clock, _ = waiting_runner(monkeypatch, ["blocked"])
    got = await runner.wait_for_decision(ACTION, AGENT, 50)
    assert got is not None and got.status == "blocked" and clock.sleeps == []


async def test_it_returns_the_latest_view_at_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, clock, _ = waiting_runner(monkeypatch, ["previewing"])
    got = await runner.wait_for_decision(ACTION, AGENT, 1.2)
    assert got is not None and got.status == "previewing"
    assert sum(clock.sleeps) == pytest.approx(1.2)


async def test_pending_ok_stops_at_a_review_and_decided_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _, _ = waiting_runner(monkeypatch, ["awaiting_review"])
    got = await runner.wait_for_decision(ACTION, AGENT, 5, until=PENDING_OK)
    assert got is not None and got.status == "awaiting_review"
    runner, clock, _ = waiting_runner(monkeypatch, ["awaiting_review"])
    await runner.wait_for_decision(ACTION, AGENT, 1, until=DECIDED)
    assert clock.sleeps  # kept polling: a review is not a decision


async def test_an_unknown_action_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, clock, _ = waiting_runner(monkeypatch, [None])
    assert await runner.wait_for_decision(ACTION, AGENT, 5) is None
    assert clock.sleeps == []


# -- drain -----------------------------------------------------------------------------------


async def test_after_drain_starts_start_and_resume_are_refused() -> None:
    runner = make_runner(FakeGraph())
    await runner.drain(0.1)
    assert not runner.accepting
    with pytest.raises(WorkflowUnavailableError) as start:
        runner.start(ACTION, AGENT)
    with pytest.raises(WorkflowUnavailableError) as resume:
        await runner.resume(ACTION, approved=True, note=None, decider=None)  # type: ignore[arg-type]
    assert start.value.reason == resume.value.reason == "draining"


async def test_a_running_task_finishes_inside_the_budget() -> None:
    gate = asyncio.Event()
    graph = FakeGraph(gate=gate)
    runner = make_runner(graph)
    runner.start(ACTION, AGENT)
    await graph.started.wait()
    asyncio.get_running_loop().call_later(0.05, gate.set)
    await runner.drain(5)
    assert graph.finished == 1


async def test_a_task_over_budget_is_cancelled_and_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed: list[Any] = []

    async def fail_if(self: WorkflowRunner, *args: Any) -> None:
        failed.append(args)

    monkeypatch.setattr(WorkflowRunner, "_fail_if", fail_if)
    graph = FakeGraph(gate=asyncio.Event())  # never opens
    runner = make_runner(graph)
    runner.start(ACTION, AGENT)
    await graph.started.wait()
    await runner.drain(0.05)
    assert runner._tasks == set() and graph.finished == 0
    assert failed == []  # left mid-flow for recovery
