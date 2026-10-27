"""Step 17: the per-agent write rate limit (BE-99, NFR-05). Over the cap a write gets `busy` and
nothing is recorded; a looping agent cannot flood `actions`."""

import asyncio
import re
import uuid

import pytest

from tests.conftest import TestAgent
from tests.mcp_write_helpers import action_ids, mcp_for
from tests.workflow_helpers import (
    FAST_LANE_SQL,
    PLANTED_EMAIL,
    WorkflowEnv,
    rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

CAP = 30


def old_actions(
    env: WorkflowEnv,
    agent_id: uuid.UUID,
    count: int,
    *,
    age_seconds: float,
    kind: str = "write",
    statement_type: str = "insert",
) -> None:
    """`count` finished actions of the agent, `age_seconds` old, each with a `received` event."""
    for _ in range(count):
        [(action_id,)] = env.db.execute(
            "INSERT INTO actions (user_id, agent_id, system_id, tool, status, statement_type,"
            " tables, created_at) VALUES (%s, %s, %s, 'query_database', 'committed', %s, '{}',"
            " clock_timestamp() - make_interval(secs => %s)) RETURNING id",
            (env.user.id, agent_id, env.system_id, statement_type, age_seconds),
        )
        env.db.execute(
            "INSERT INTO audit_events (action_id, event_type, data)"
            " VALUES (%s, 'received', jsonb_build_object('kind', %s::text))",
            (action_id, kind),
        )


def seconds_in(message: str) -> int:
    match = re.fullmatch(
        r"Blastproof is busy previewing other writes\. Retry in about (\d+) seconds\.", message
    )
    assert match, message
    return int(match.group(1))


async def test_the_write_after_the_cap_gets_busy_and_records_nothing(
    wf_env: WorkflowEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        # Inserting 30 rows takes a second or two, so "59 s old" would age out while it runs.
        old_actions(wf_env, api.agent.id, CAP, age_seconds=50)
        result = await api.query(
            f"INSERT INTO customer_notes (user_id, body) VALUES (1, '{PLANTED_EMAIL}')"
        )
        assert result["action_id"] is None and result["status"] is None
        assert 1 <= seconds_in(result["message"]) <= 10
        assert len(action_ids(wf_env, api.agent)) == CAP  # nothing was recorded
    output = capsys.readouterr().out
    assert '"event": "rate_limited"' in output
    assert PLANTED_EMAIL not in output and "customer_notes" not in output


async def test_the_busy_seconds_say_when_the_oldest_write_leaves_the_window(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        old_actions(wf_env, api.agent.id, CAP - 1, age_seconds=10)
        old_actions(wf_env, api.agent.id, 1, age_seconds=40)
        result = await api.query(FAST_LANE_SQL)
    assert 19 <= seconds_in(result["message"]) <= 20


async def test_a_write_just_under_the_cap_goes_through(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        old_actions(wf_env, api.agent.id, CAP - 1, age_seconds=30)
        result = await api.query(FAST_LANE_SQL)
    assert result["status"] == "committed"


async def test_writes_older_than_a_minute_do_not_count(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        old_actions(wf_env, api.agent.id, CAP, age_seconds=61)
        result = await api.query(FAST_LANE_SQL)
    assert result["status"] == "committed"


@pytest.mark.usefixtures("company_db_seeded")
async def test_reads_do_not_count(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        old_actions(wf_env, api.agent.id, CAP, age_seconds=5, kind="read", statement_type="select")
        for _ in range(3):  # and reads are never limited themselves
            assert (await api.query("SELECT 1"))["status"] == "executed"
        assert (await api.query(FAST_LANE_SQL))["status"] == "committed"


async def test_a_write_that_looks_like_a_select_counts(wf_env: WorkflowEnv) -> None:
    """The count follows the `received` event's kind, not `statement_type` (a SELECT calling an
    unknown function is a write)."""
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        old_actions(wf_env, api.agent.id, CAP, age_seconds=5, kind="write", statement_type="select")
        result = await api.query(FAST_LANE_SQL)
    assert result["status"] is None and "busy" in result["message"]


async def test_a_sibling_agent_has_its_own_limit(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        sibling: TestAgent = wf_env.db.make_agent(wf_env.user.id)
        old_actions(wf_env, api.agent.id, CAP, age_seconds=5)
        assert (await api.query(FAST_LANE_SQL))["status"] is None
        assert (await api.query(FAST_LANE_SQL, agent=sibling))["status"] == "committed"


async def test_six_writes_at_once_make_exactly_three_actions(wf_env: WorkflowEnv) -> None:
    """The per-agent lock: the count each call sees includes the ones before it."""
    settings = {"max_writes_per_minute_per_agent": 3}
    async with (
        rig(wf_env, settings=settings) as r,
        mcp_for(wf_env, runner_for(r), **settings) as api,
    ):
        results = await asyncio.gather(*(api.query(FAST_LANE_SQL) for _ in range(6)))
        assert len(action_ids(wf_env, api.agent)) == 3
    assert sorted(str(x["status"]) for x in results) == ["None"] * 3 + ["committed"] * 3
    for result in results:
        if result["status"] is None:
            assert result["action_id"] is None
            seconds_in(result["message"])
