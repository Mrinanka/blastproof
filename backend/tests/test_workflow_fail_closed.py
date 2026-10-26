"""CLAUDE.md, architecture rule 1: any error blocks the write. An exception in each wrapped node
ends the action as `failed` with decision `block`, a `fail_closed` event and a log line, and the
graph returns normally. Nothing is committed, and no log line holds SQL, intent or row values.
"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from langgraph.errors import GraphInterrupt
from structlog.testing import capture_logs

import app.workflow.graph as graph_module
from app.db.enums import ActionStatus
from app.services.policy import PolicyFactsError
from app.services.shadow import Diff, ShadowBusyError, ShadowFailedError, TableDiff
from app.workflow.graph import _Nodes
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL, replit_diff
from tests.judge_helpers import FakeJudgeLlm, verdict
from tests.workflow_helpers import (
    FAST_LANE_SQL,
    PLANTED_EMAIL,
    READ_SQL,
    SMALL_INSERT_SQL,
    FakeKnowledge,
    FakeShadowRunner,
    RecordingCommitter,
    WorkflowEnv,
    action_row,
    checkpoint_text,
    claim,
    events,
    new_action,
    rig,
    status_history,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

PRODUCTS_INSERT = Diff(tables={"products": TableDiff("products", inserted=1, rows_before=40)})


def boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(f"secret detail {PLANTED_EMAIL}")


async def aboom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(f"secret detail {PLANTED_EMAIL}")


def fail_closed_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == "fail_closed"]


def assert_failed_closed(
    env: WorkflowEnv, action_id: uuid.UUID, logs: list[dict[str, Any]], *, node: str, reason: str
) -> None:
    row = action_row(env, action_id)
    assert row["status"] == "failed"
    assert row["decision"] == "block"
    assert row["failure_reason"] == reason
    assert row["total_ms"] is not None
    recorded = [data for kind, data in events(env, action_id) if kind == "fail_closed"]
    assert len(recorded) == 1
    assert recorded[0]["reason"] == reason and recorded[0]["node"] == node
    [line] = fail_closed_logs(logs)
    assert line["reason_code"] == reason and line["node"] == node
    # Only the exception class, never its message.
    assert PLANTED_EMAIL not in str(logs)
    assert PLANTED_EMAIL not in str(events(env, action_id))


# Each row: node, the SQL to run, the diff the fake shadow run returns, and how to break it.
NODE_FAILURES: list[Any] = [
    pytest.param(
        "classify",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(graph_module, "classify_in_worker", aboom),
        id="classify",
    ),
    pytest.param(
        "pre_rules",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(graph_module, "evaluate_pass_one", boom),
        id="pre_rules",
    ),
    pytest.param(
        "fast_lane",
        FAST_LANE_SQL,
        lambda r, mp: mp.setattr(r.deps.impact, "template", boom),
        id="fast_lane",
    ),
    pytest.param(
        "post_rules",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(graph_module, "evaluate_pass_two", boom),
        id="post_rules",
    ),
    pytest.param(
        "retrieve",
        SMALL_INSERT_SQL,
        lambda r, mp: setattr(r.deps.knowledge, "retrieve", aboom),
        id="retrieve",
    ),
    pytest.param(
        "impact",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(r.deps.impact, "summarize", aboom),
        id="impact",
    ),
    pytest.param(
        "judge",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(graph_module, "save_judge", aboom),
        id="judge",
    ),
    pytest.param(
        "decide",
        SMALL_INSERT_SQL,
        lambda r, mp: mp.setattr(graph_module, "decide", boom),
        id="decide",
    ),
    pytest.param(
        "commit",
        SMALL_INSERT_SQL,
        lambda r, mp: setattr(r.committer, "raises", RuntimeError(PLANTED_EMAIL)),
        id="commit",
    ),
]


@pytest.mark.parametrize(("node", "sql", "inject"), NODE_FAILURES)
async def test_an_exception_in_a_node_fails_closed(
    wf_env: WorkflowEnv,
    monkeypatch: pytest.MonkeyPatch,
    node: str,
    sql: str,
    inject: Callable[[Any, pytest.MonkeyPatch], None],
) -> None:
    async with rig(wf_env, diff=PRODUCTS_INSERT) as r:
        action_id = new_action(wf_env, sql)
        inject(r, monkeypatch)
        with capture_logs() as logs:
            await r.run(action_id)  # returns normally: no exception reaches the caller

        assert_failed_closed(wf_env, action_id, logs, node=node, reason="workflow_error")
        assert r.committer.committed == []
        assert await r.next_nodes(action_id) == ()


async def test_a_failing_shadow_node_fails_closed_after_previewing(
    wf_env: WorkflowEnv,
) -> None:
    shadow = FakeShadowRunner(raises=RuntimeError(PLANTED_EMAIL))
    async with rig(wf_env, shadow=shadow) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        with capture_logs() as logs:
            await r.run(action_id)
        assert_failed_closed(wf_env, action_id, logs, node="shadow", reason="workflow_error")
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "failed",
        ]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ShadowBusyError(), "shadow_busy"),
        (ShadowBusyError("neon_branch_limit"), "neon_branch_limit"),
        (ShadowFailedError("shadow_timeout"), "shadow_timeout"),
        (ShadowFailedError("unknown_table"), "unknown_table"),
    ],
)
async def test_shadow_errors_end_as_failed_with_their_reason_code(
    wf_env: WorkflowEnv, error: Exception, reason: str
) -> None:
    async with rig(wf_env, shadow=FakeShadowRunner(raises=error)) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        with capture_logs() as logs:
            await r.run(action_id)
        assert_failed_closed(wf_env, action_id, logs, node="shadow", reason=reason)
        assert r.summary_llm.calls == [] and r.judge_llm.calls == []


async def test_bad_diff_facts_fail_closed(
    wf_env: WorkflowEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def facts_error(*_a: Any, **_k: Any) -> Any:
        raise PolicyFactsError("orders: changed but its total row count is missing")

    async with rig(wf_env, diff=PRODUCTS_INSERT) as r:
        monkeypatch.setattr(graph_module, "evaluate_pass_two", facts_error)
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        with capture_logs() as logs:
            await r.run(action_id)
        assert_failed_closed(wf_env, action_id, logs, node="post_rules", reason="policy_facts")
        assert r.judge_llm.calls == []


async def test_a_read_given_to_the_graph_fails_closed(wf_env: WorkflowEnv) -> None:
    """ARCH-10: only writes enter the graph."""
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, READ_SQL)
        with capture_logs() as logs:
            await r.run(action_id)
        assert_failed_closed(wf_env, action_id, logs, node="classify", reason="not_a_write")
        assert status_history(wf_env, action_id) == ["received", "failed"]
        assert r.shadow.calls == []


async def test_an_unknown_action_id_does_not_raise(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        with capture_logs() as logs:
            await r.run(uuid.uuid4())
        assert fail_closed_logs(logs)[0]["reason_code"] == "action_not_found"


async def test_a_judge_that_raises_is_not_a_node_failure(wf_env: WorkflowEnv) -> None:
    """`assess_safely` absorbs it: review with the judge unavailable, not `failed`."""
    async with rig(wf_env, diff=PRODUCTS_INSERT, judge_llm=FakeJudgeLlm(raises=KeyError("x"))) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_a_retrieval_error_is_not_a_node_failure(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=PRODUCTS_INSERT, knowledge=FakeKnowledge(fails=True)) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_a_failing_commit_leaves_the_action_failed_not_committed(
    wf_env: WorkflowEnv,
) -> None:
    committer = RecordingCommitter(raises=RuntimeError("disk full"))
    async with rig(wf_env, diff=replit_diff(), committer=committer) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
        claim(wf_env, action_id, approved=True)
        await r.resume(action_id, approved=True)

        row = action_row(wf_env, action_id)
        assert row["status"] == "failed" and row["failure_reason"] == "workflow_error"
        assert status_history(wf_env, action_id)[-3:] == ["approved", "committing", "failed"]


# --- the wrapper itself -------------------------------------------------------------------------


async def test_the_wrapper_lets_interrupts_and_cancellation_through(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        nodes = _Nodes(r.deps)
        state: Any = {"action_id": str(uuid.uuid4())}

        async def interrupted(_state: Any) -> dict[str, Any]:
            raise GraphInterrupt()

        async def cancelled(_state: Any) -> dict[str, Any]:
            raise asyncio.CancelledError()

        with pytest.raises(GraphInterrupt):
            await nodes.guarded("x", interrupted)(state)
        with pytest.raises(asyncio.CancelledError):
            await nodes.guarded("x", cancelled)(state)


async def test_the_wrapper_adds_the_nodes_time(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        nodes = _Nodes(r.deps)

        async def fine(_state: Any) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return {"route": "preview"}

        out = await nodes.guarded("x", fine)({"action_id": str(uuid.uuid4()), "work_ms": 100})  # type: ignore[typeddict-item]
        assert out["route"] == "preview"
        assert out["work_ms"] >= 140


async def test_a_terminal_action_is_not_moved_to_failed(wf_env: WorkflowEnv) -> None:
    """If the action already ended (blocked), a later failure writes the event but no status."""
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, "DROP TABLE users")
        await r.run(action_id)
        assert action_row(wf_env, action_id)["status"] == ActionStatus.BLOCKED
        nodes = _Nodes(r.deps)
        await nodes._fail({"action_id": str(action_id)}, "block", RuntimeError("late"), 0.0)  # type: ignore[typeddict-item]
        assert action_row(wf_env, action_id)["status"] == "blocked"
        assert [k for k, _ in events(wf_env, action_id)][-1] == "fail_closed"


# --- logs and checkpoints hold no raw agent text ------------------------------------------------


async def test_no_log_line_or_checkpoint_holds_the_sql_the_intent_or_row_values(
    wf_env: WorkflowEnv,
) -> None:
    sql = f"INSERT INTO products (name, price_cents) VALUES ('{PLANTED_EMAIL}', 1999)"
    intent = f"Add the product from {PLANTED_EMAIL} please"
    judge = FakeJudgeLlm(parsed=verdict("allow", "low", 0.9, "Routine."))
    async with rig(wf_env, diff=PRODUCTS_INSERT, judge_llm=judge) as r:
        action_id = new_action(wf_env, sql, intent=intent)
        with capture_logs() as logs:
            await r.run(action_id)

        assert action_row(wf_env, action_id)["status"] == "committed"
        assert PLANTED_EMAIL not in str(logs)
        stored = checkpoint_text(wf_env, action_id)
        assert stored  # the search below is not vacuous
        assert "planted.secret" not in stored
        assert "[masked]" in stored or "[email]" in stored  # only the masked display is there
        row = action_row(wf_env, action_id)
        assert PLANTED_EMAIL not in (row["sql_display"] or "")
        # The raw intent never reached a model prompt either (it is masked first).
        assert all(PLANTED_EMAIL not in str(call) for call in r.summary_llm.calls)
        assert all(PLANTED_EMAIL not in str(call) for call in r.judge_llm.calls)
