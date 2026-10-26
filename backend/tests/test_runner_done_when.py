"""DW-B15, automated: one decision wins a race; an expired review cannot be approved; a restart
loses and repeats nothing."""

import asyncio
import uuid
from datetime import timedelta

import pytest

from app.core.clock import FrozenClock
from app.workflow.runner import AlreadyDecidedError
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    claim,
    decider_for,
    events,
    new_action,
    paused_action,
    paused_rig,
    rig,
    runner_for,
    status_history,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


def decision_events(env: WorkflowEnv, action_id: uuid.UUID, kind: str) -> list[dict]:
    return [data for event, data in events(env, action_id) if event == kind]


async def test_two_approvals_at_once_commit_once(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        who = decider_for(wf_env.user)
        results = await asyncio.gather(
            runner.resume(action_id, approved=True, note=None, decider=who),
            runner.resume(action_id, approved=True, note=None, decider=who),
            return_exceptions=True,
        )
        await runner.join()
        assert sum(isinstance(x, AlreadyDecidedError) for x in results) == 1
        assert sum(not isinstance(x, BaseException) for x in results) == 1
        assert r.committer.committed == [action_id]
        assert status_history(wf_env, action_id).count("approved") == 1
        assert action_row(wf_env, action_id)["status"] == "committed"


async def test_approve_against_reject_has_one_winner(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        who = decider_for(wf_env.user)
        results = await asyncio.gather(
            runner.resume(action_id, approved=True, note=None, decider=who),
            runner.resume(action_id, approved=False, note="not today, thanks", decider=who),
            return_exceptions=True,
        )
        await runner.join()
        assert sum(isinstance(x, AlreadyDecidedError) for x in results) == 1
        approved = not isinstance(results[0], BaseException)
        assert r.committer.committed == ([action_id] if approved else [])
        assert action_row(wf_env, action_id)["status"] == ("committed" if approved else "rejected")


async def test_approve_against_the_sweep_has_one_winner(wf_env: WorkflowEnv) -> None:
    """Past the deadline, whichever runs first, the review is never approved."""
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        late = FrozenClock(action_row(wf_env, action_id)["expires_at"] + timedelta(seconds=1))
        runner = runner_for(r, clock=late)
        results = await asyncio.gather(
            runner.resume(action_id, approved=True, note=None, decider=decider_for(wf_env.user)),
            runner.sweep(),
            return_exceptions=True,
        )
        await runner.join()
        assert isinstance(results[0], AlreadyDecidedError)
        assert r.committer.committed == []
        row = action_row(wf_env, action_id)
        assert row["status"] == "expired" and row["decided_by"] is None
        assert status_history(wf_env, action_id).count("expired") == 1


async def test_an_expired_review_cannot_be_approved_after_the_sweep(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        late = FrozenClock(action_row(wf_env, action_id)["expires_at"] + timedelta(seconds=1))
        runner = runner_for(r, clock=late)
        await runner.sweep()
        with pytest.raises(AlreadyDecidedError) as raised:
            await runner.resume(
                action_id, approved=True, note=None, decider=decider_for(wf_env.user)
            )
        assert (raised.value.status, raised.value.decided_by_name) == ("expired", None)
        assert r.committer.committed == []


async def test_an_overdue_review_not_yet_swept_is_refused_and_expired(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        late = FrozenClock(action_row(wf_env, action_id)["expires_at"] + timedelta(seconds=1))
        runner = runner_for(r, clock=late)
        with pytest.raises(AlreadyDecidedError) as raised:
            await runner.resume(
                action_id, approved=True, note=None, decider=decider_for(wf_env.user)
            )
        assert raised.value.status == "expired"
        row = action_row(wf_env, action_id)
        assert row["status"] == "expired" and row["decided_by"] is None
        assert row["decided_at"] is not None
        assert decision_events(wf_env, action_id, "expired") == [{"reason": "review_timeout"}]
        await runner.join()
        assert r.committer.committed == []


async def test_a_claimed_but_unresumed_action_is_committed_once_after_a_restart(
    wf_env: WorkflowEnv,
) -> None:
    async with paused_rig(wf_env) as first:
        action_id = await paused_action(first)
        assert claim(wf_env, action_id, approved=True) == 1  # the process "dies" before resuming
    async with rig(wf_env) as second:  # a new pool, saver and graph
        runner = runner_for(second, recovered=False)
        assert not runner.accepting
        await runner.recover()
        await runner.join()
        assert runner.accepting
        assert second.committer.committed == [action_id]
        assert second.shadow.calls == []
        assert action_row(wf_env, action_id)["status"] == "committed"
        await runner.recover()  # a second recovery commits nothing more
        await runner.join()
        assert second.committer.committed == [action_id]


async def test_in_flight_actions_fail_with_restart(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    ids = {}
    for status in ("received", "classified", "previewing"):
        action_id = new_action(env, "DELETE FROM orders WHERE id > 0")
        env.db.execute("UPDATE actions SET status = %s WHERE id = %s", (status, action_id))
        ids[status] = action_id
    async with rig(env) as r:
        report = await runner_for(r, recovered=False).recover()
    assert report is not None and report.failed == 3
    for action_id in ids.values():
        row = action_row(env, action_id)
        assert (row["status"], row["failure_reason"], row["decision"]) == (
            "failed",
            "restart",
            "block",
        )
