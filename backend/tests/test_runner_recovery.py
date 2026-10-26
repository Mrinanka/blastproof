"""`WorkflowRunner.recover`: what a restart does with each status."""

import uuid
from typing import Any

import psycopg
import pytest
from langgraph.types import Command

from app.workflow.graph import run_config
from app.workflow.runner import RUNNER_LOCK_KEY
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    claim,
    new_action,
    paused_action,
    paused_rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]
SQL = "DELETE FROM orders WHERE id > 0"


def set_status(env: WorkflowEnv, status: str) -> uuid.UUID:
    action_id = new_action(env, SQL)
    env.db.execute("UPDATE actions SET status = %s WHERE id = %s", (status, action_id))
    return action_id


async def test_a_rejected_action_never_resumed_ends_its_thread(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    async with paused_rig(env) as first:
        action_id = await paused_action(first)
        claim(env, action_id, approved=False)
    async with paused_rig(env) as r:
        runner = runner_for(r, recovered=False)
        report = await runner.recover()
        await runner.join()
        assert report is not None and report.resumed == 1
        assert await r.next_nodes(action_id) == ()
        assert r.committer.committed == [] and action_row(env, action_id)["status"] == "rejected"


async def test_an_approved_action_past_the_interrupt_is_committed_once(
    fresh_wf_env: WorkflowEnv,
) -> None:
    env = fresh_wf_env
    async with paused_rig(env) as first:
        action_id = await paused_action(first)
        claim(env, action_id, approved=True)
        config: Any = run_config(action_id, env.agent_id, "test")
        await first.graph.ainvoke(
            Command(resume={"approved": True}),
            config,
            interrupt_before=["commit"],
            durability="sync",
            version="v2",
        )
        assert await first.next_nodes(action_id) == ("commit",)
        assert first.committer.committed == []
    async with paused_rig(env) as r:
        runner = runner_for(r, recovered=False)
        report = await runner.recover()
        await runner.join()
        assert report is not None and report.continued == 1
        assert r.committer.committed == [action_id]
        assert action_row(env, action_id)["status"] == "committed"


async def test_no_thread_approved_fails_rejected_stays(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    approved, rejected = set_status(env, "approved"), set_status(env, "rejected")
    async with paused_rig(env) as r:
        await runner_for(r, recovered=False).recover()
    row = action_row(env, approved)
    assert (row["status"], row["failure_reason"]) == ("failed", "restart")
    assert action_row(env, rejected)["status"] == "rejected"


async def test_a_paused_review_is_untouched_and_an_unpaused_one_fails(
    fresh_wf_env: WorkflowEnv,
) -> None:
    env = fresh_wf_env
    async with paused_rig(env) as first:
        paused = await paused_action(first)
    stray = set_status(env, "awaiting_review")
    async with paused_rig(env) as r:
        await runner_for(r, recovered=False).recover()
    assert action_row(env, paused)["status"] == "awaiting_review"
    row = action_row(env, stray)
    assert (row["status"], row["failure_reason"]) == ("failed", "restart")


async def test_committing_and_terminal_statuses_are_untouched(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    names = ("committing", "committed", "blocked", "expired", "failed")
    ids = {status: set_status(env, status) for status in names}
    async with paused_rig(env) as r:
        report = await runner_for(r, recovered=False).recover()
    assert report is not None and report.untouched_committing == 1
    for status, action_id in ids.items():
        assert action_row(env, action_id)["status"] == status


async def test_a_held_lock_changes_nothing_and_does_not_accept(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    action_id = set_status(env, "received")
    with psycopg.connect(env.db.app_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (RUNNER_LOCK_KEY,))
        async with paused_rig(env) as r:
            runner = runner_for(r, recovered=False)
            assert await runner.recover() is None
            assert not runner.accepting
    assert action_row(env, action_id)["status"] == "received"


async def test_recovery_twice_is_a_no_op(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    set_status(env, "received")
    async with paused_rig(env) as r:
        runner = runner_for(r, recovered=False)
        first = await runner.recover()
        second = await runner.recover()
    assert first is not None and first.failed == 1
    assert second is not None and (second.failed, second.resumed, second.continued) == (0, 0, 0)
