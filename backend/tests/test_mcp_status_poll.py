"""Step 17: `get_action_status` as a long poll, and who may see an action (BE-98, SEC-34)."""

import asyncio
import time
import uuid
from typing import Any

import pytest

from app.services import agent_messages
from app.services.commit import CommitDriftError, CommitOutcome
from tests.impact_helpers import replit_diff
from tests.mcp_write_helpers import action_ids, mcp_for
from tests.workflow_helpers import (
    FAST_LANE_SQL,
    REPLIT_SQL,
    RecordingCommitter,
    WorkflowEnv,
    decider_for,
    rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

REPLIT_INTENT = "Clean up inactive users"
NOTE = "Too broad, exclude paying customers."


def display_name(env: WorkflowEnv) -> str:
    [(name,)] = env.db.execute("SELECT display_name FROM users WHERE id = %s", (env.user.id,))
    return name


# --- waiting ------------------------------------------------------------------------------------


async def test_a_waiting_poll_returns_as_soon_as_the_reviewer_rejects(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        runner = runner_for(r)
        async with mcp_for(wf_env, runner) as api:
            held = await api.query(REPLIT_SQL, REPLIT_INTENT)
            action_id = held["action_id"]
            poll = asyncio.create_task(api.status(action_id, wait_seconds=30))
            await asyncio.sleep(1.0)
            assert not poll.done()  # still waiting for the reviewer
            decided_at = time.monotonic()
            await runner.resume(
                uuid.UUID(action_id), approved=False, note=NOTE, decider=decider_for(wf_env.user)
            )
            result = await asyncio.wait_for(poll, timeout=5)
            waited = time.monotonic() - decided_at
            await runner.join()
    assert waited < 2
    assert result["status"] == "rejected"
    assert result["message"] == agent_messages.rejected(display_name(wf_env), NOTE, action_id)


async def test_wait_seconds_zero_returns_at_once(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        started = time.monotonic()
        result = await api.status(held["action_id"], wait_seconds=0)
    assert time.monotonic() - started < 2
    assert result["status"] == "awaiting_review"
    assert result["message"] == agent_messages.pending(held["action_id"])


@pytest.mark.usefixtures("company_db_seeded")
async def test_an_executed_read_does_not_wait_out_the_poll(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        read = await api.query("SELECT 1")
        started = time.monotonic()
        result = await api.status(read["action_id"], wait_seconds=50)
    assert time.monotonic() - started < 2
    assert result["status"] == "executed" and result["message"] is None


async def test_a_poll_returns_at_the_wait_limit_with_the_review_still_open(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        started = time.monotonic()
        result = await api.status(held["action_id"], wait_seconds=2)
    assert 1.5 < time.monotonic() - started < 6
    assert result["status"] == "awaiting_review"


async def test_polls_create_no_action(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        before = action_ids(wf_env, api.agent)
        await api.status(held["action_id"], wait_seconds=1)
        await api.status(uuid.uuid4(), wait_seconds=1)
        assert action_ids(wf_env, api.agent) == before


# --- the polling budget, expiry and drift -------------------------------------------------------


async def test_a_review_older_than_the_budget_says_still_waiting_without_waiting(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        wf_env.db.execute(
            "UPDATE actions SET created_at = now() - interval '11 minutes' WHERE id = %s",
            (held["action_id"],),
        )
        started = time.monotonic()
        result = await api.status(held["action_id"], wait_seconds=50)
    assert time.monotonic() - started < 2
    assert result["status"] == "awaiting_review"
    assert result["message"] == agent_messages.still_waiting(held["action_id"], 11)


async def test_an_expired_review_names_the_hours(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        wf_env.db.execute(
            "UPDATE actions SET status = 'expired' WHERE id = %s", (held["action_id"],)
        )
        result = await api.status(held["action_id"])
    assert result["status"] == "expired"
    assert result["message"] == agent_messages.expired(24)


class DriftOnce(RecordingCommitter):
    """The company data changed between preview and commit, the first time."""

    def __init__(self) -> None:
        super().__init__()
        self.drifted = False

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        if not self.drifted:
            self.drifted = True
            raise CommitDriftError("rows_changed", {"expected": 1, "found": 2})
        return await super().commit(action_id)


async def test_a_drift_sends_it_back_to_review_and_the_agent_is_told(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff(), committer=DriftOnce()) as r:
        runner = runner_for(r)
        async with mcp_for(wf_env, runner) as api:
            held = await api.query(REPLIT_SQL, REPLIT_INTENT)
            action_id = uuid.UUID(held["action_id"])
            await runner.resume(
                action_id, approved=True, note=None, decider=decider_for(wf_env.user)
            )
            await runner.join()
            result = await api.status(action_id, wait_seconds=0)
    assert result["status"] == "awaiting_review"
    assert result["message"] == (
        f"{agent_messages.drifted(str(action_id))} {agent_messages.KEEP_POLLING}"
    )


async def test_a_poll_started_during_the_commit_returns_at_the_drifted_review(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=replit_diff(), committer=DriftOnce()) as r:
        runner = runner_for(r)
        async with mcp_for(wf_env, runner) as api:
            held = await api.query(REPLIT_SQL, REPLIT_INTENT)
            action_id = uuid.UUID(held["action_id"])
            wf_env.db.execute("UPDATE actions SET status = 'approved' WHERE id = %s", (action_id,))
            started = time.monotonic()
            poll = asyncio.create_task(api.status(action_id, wait_seconds=20))
            await asyncio.sleep(0.8)
            wf_env.db.execute(
                "UPDATE actions SET status = 'awaiting_review' WHERE id = %s", (action_id,)
            )
            result: dict[str, Any] = await asyncio.wait_for(poll, timeout=10)
    assert time.monotonic() - started < 8
    assert result["status"] == "awaiting_review"


# --- who may ask --------------------------------------------------------------------------------


async def test_another_owners_action_is_not_found_by_status_or_retry_of(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, REPLIT_INTENT)
        stranger = wf_env.db.make_agent(wf_env.db.make_user().id)
        admin = wf_env.db.make_agent(wf_env.db.make_user(role="admin").id)
        sibling = wf_env.db.make_agent(wf_env.user.id)
        missing = str(uuid.uuid4())
        for other in (stranger, admin):
            for bad in (held["action_id"], missing, "not-a-uuid", ""):
                status = await api.status(bad, agent=other)
                assert status["status"] is None
                assert status["message"] == agent_messages.not_found(bad)
                if bad:  # an empty retry_of is no retry_of: an ordinary write
                    retry = await api.query(FAST_LANE_SQL, "again", retry_of=bad, agent=other)
                    assert retry["action_id"] is None and retry["status"] is None
                    assert retry["message"] == agent_messages.not_found(bad)
        assert action_ids(wf_env, stranger) == [] and action_ids(wf_env, admin) == []
        # Agents of the same owner share their actions (D-055).
        assert (await api.status(held["action_id"], agent=sibling))["status"] == "awaiting_review"
