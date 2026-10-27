"""Step 17: a write through `query_database` is previewed, judged, held or committed (BE-98, BE-100,
SEC-20, ARCH-10, OBS-06), on the real graph and the real MCP route.

The runner and graph come from the workflow rig (fake shadow run, models and commit; real Postgres
for actions, events and checkpoints).
"""

import asyncio
import uuid
from typing import Any

import pytest

from app.services import agent_messages
from app.services.shadow import ShadowResult
from app.workflow.runner import WorkflowRunner, WorkflowUnavailableError
from tests.impact_helpers import replit_diff
from tests.judge_helpers import FakeJudgeLlm
from tests.mcp_write_helpers import action_ids, mcp_for
from tests.workflow_helpers import (
    DROP_SQL,
    FAST_LANE_SQL,
    PLANTED_EMAIL,
    READ_SQL,
    REPLIT_SQL,
    SMALL_INSERT_SQL,
    FakeShadowRunner,
    WorkflowEnv,
    action_row,
    checkpoint_count,
    event_types,
    rig,
    runner_for,
    status_history,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

REPLIT_INTENT = "Clean up inactive users"


def only_action(env: WorkflowEnv, api: Any) -> uuid.UUID:
    [action_id] = action_ids(env, api.agent)
    return action_id


# --- the outcomes -------------------------------------------------------------------------------


async def test_a_fast_lane_insert_is_committed_with_its_count(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query(FAST_LANE_SQL, "Log a call")
        action_id = only_action(wf_env, api)
    assert result["status"] == "committed"
    assert result["decision"] == "allow"
    assert result["action_id"] == str(action_id)
    assert result["message"] == "Done: 1 rows inserted in customer_notes."
    assert r.committer.committed == [action_id]
    assert r.shadow.calls == []  # the fast lane skips the preview


async def test_the_replit_delete_is_held_for_review_with_a_checkpoint(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query(REPLIT_SQL, REPLIT_INTENT)
        action_id = only_action(wf_env, api)
        assert await r.next_nodes(action_id) == ("human_review",)
    assert result["status"] == "awaiting_review"
    assert result["decision"] == "review"
    assert result["message"] == agent_messages.pending(str(action_id))
    assert checkpoint_count(wf_env, action_id) > 0
    assert r.committer.committed == []


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        (DROP_SQL, "DDL"),
        ("DELETE FROM users", "WHERE"),
        ("SELECT pg_terminate_backend(1)", ""),
    ],
    ids=["drop", "delete-without-where", "side-effect-function"],
)
async def test_blocked_writes_never_reach_the_preview(
    wf_env: WorkflowEnv, sql: str, reason: str
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query(sql, "do it")
        action_id = only_action(wf_env, api)
    assert result["status"] == "blocked"
    assert result["decision"] == "block"
    assert result["message"].startswith("Blocked: ")
    assert result["message"].endswith(". Do not retry the same statement.")
    assert ".." not in result["message"]
    assert r.shadow.calls == []
    assert r.committer.committed == []
    assert action_row(wf_env, action_id)["status"] == "blocked"


async def test_the_statement_is_blocked_not_run_as_a_read(wf_env: WorkflowEnv) -> None:
    """A SELECT with a side-effect function goes to the graph, not the read path (SEC-20)."""
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query("SELECT pg_terminate_backend(1)", "probe")
        action_id = only_action(wf_env, api)
    assert result["status"] == "blocked"
    assert "classified" in event_types(wf_env, action_id)
    assert "executed" not in event_types(wf_env, action_id)


async def test_a_judge_that_fails_holds_the_write_and_says_so(wf_env: WorkflowEnv) -> None:
    judge = FakeJudgeLlm(raises=RuntimeError("model exploded"))
    async with (
        rig(wf_env, diff=replit_diff(), judge_llm=judge) as r,
        mcp_for(wf_env, runner_for(r)) as api,
    ):
        result = await api.query(REPLIT_SQL, REPLIT_INTENT)
        action_id = only_action(wf_env, api)
    assert result["status"] == "awaiting_review"
    assert result["message"] == (
        f"{agent_messages.ai_unavailable(str(action_id))} {agent_messages.KEEP_POLLING}"
    )


class SlowShadow(FakeShadowRunner):
    """A preview that takes a while, like the Replit delete on Neon (D-142)."""

    def __init__(self, diff: Any, delay: float) -> None:
        super().__init__(diff)
        self.delay = delay

    async def run(self, *, action_id: uuid.UUID, profile: str, classification: Any) -> ShadowResult:
        await asyncio.sleep(self.delay)
        return await super().run(
            action_id=action_id, profile=profile, classification=classification
        )


async def test_a_slow_preview_is_in_progress_and_polling_reaches_the_review(
    wf_env: WorkflowEnv,
) -> None:
    shadow = SlowShadow(replit_diff(), delay=2.5)
    async with (
        rig(wf_env, shadow=shadow, settings={"review_wait_seconds": 1}) as r,
        mcp_for(wf_env, runner_for(r), review_wait_seconds=1) as api,
    ):
        first = await api.query(REPLIT_SQL, REPLIT_INTENT)
        action_id = only_action(wf_env, api)
        assert first["status"] == "previewing"
        assert first["message"] == agent_messages.in_progress(str(action_id))
        # Not "held for review": the preview has not finished.
        assert "human review" not in first["message"]
        second = await api.status(action_id, wait_seconds=20)
    assert second["status"] == "awaiting_review"
    assert second["message"] == agent_messages.pending(str(action_id))


# --- reads --------------------------------------------------------------------------------------


async def test_a_read_creates_no_checkpoint_and_stores_what_ran(wf_env: WorkflowEnv) -> None:
    sql = f"SELECT count(*) FROM users WHERE email = '{PLANTED_EMAIL}'"
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query(sql, "look up one user")
        action_id = only_action(wf_env, api)
    assert result["status"] == "executed"
    assert result["rows"] == [[0]]
    assert checkpoint_count(wf_env, action_id) == 0  # ARCH-10: reads never enter the graph
    assert r.shadow.calls == []
    row = action_row(wf_env, action_id)
    assert row["status"] == "executed"
    assert row["statement_type"] == "select"
    assert row["tables"] == ["users"]
    assert row["pinned_sql"] and PLANTED_EMAIL in row["pinned_sql"]
    assert row["sql_display"] and PLANTED_EMAIL not in row["sql_display"]  # masked (ARCH-17)
    assert status_history(wf_env, action_id) == ["received", "executed"]


async def test_a_read_works_with_no_runner(wf_env: WorkflowEnv) -> None:
    async with mcp_for(wf_env, None) as api:
        result = await api.query(READ_SQL)
    assert result["status"] == "executed" and result["rows"][0][0] > 0


# --- no action is recorded ----------------------------------------------------------------------


async def test_no_runner_recovering_and_draining_record_nothing(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        recovering = runner_for(r, recovered=False)
        draining = runner_for(r)
        await draining.drain(timeout=0)
        cases = [
            (None, agent_messages.failed("workflow_not_configured"), "failed"),
            (recovering, agent_messages.busy(10), None),
            (draining, agent_messages.busy(10), None),
        ]
        for runner, message, status in cases:
            async with mcp_for(wf_env, runner) as api:
                result = await api.query(SMALL_INSERT_SQL, "add a product")
                assert result["action_id"] is None
                assert result["status"] == status
                assert result["message"] == message
                assert action_ids(wf_env, api.agent) == []
    assert r.committer.committed == [] and r.shadow.calls == []


class RacingRunner(WorkflowRunner):
    """Accepting when asked, but drain begins before `start` (the check-then-act race)."""

    def start(self, action_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        raise WorkflowUnavailableError("draining")


async def test_losing_the_start_race_fails_the_action_and_says_busy(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        racing = RacingRunner(r.graph, r.deps.sessionmaker, r.deps.clock, r.settings)
        racing.mark_recovered()
        async with mcp_for(wf_env, racing) as api:
            result = await api.query(SMALL_INSERT_SQL, "add a product")
            action_id = only_action(wf_env, api)
    assert result["action_id"] == str(action_id)
    assert result["message"] == agent_messages.busy(10)
    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "workflow_unavailable"
    assert status_history(wf_env, action_id) == ["received", "failed"]


# --- the record ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "diff_kind", "history"),
    [
        (FAST_LANE_SQL, None, ["received", "classified", "approved", "committing", "committed"]),
        (DROP_SQL, None, ["received", "classified", "blocked"]),
        (
            REPLIT_SQL,
            "replit",
            ["received", "classified", "previewing", "awaiting_review"],
        ),
    ],
    ids=["committed", "blocked", "held"],
)
async def test_the_timeline_equals_the_status_history(
    wf_env: WorkflowEnv, sql: str, diff_kind: str | None, history: list[str]
) -> None:
    diff = replit_diff() if diff_kind else None
    async with rig(wf_env, diff=diff) as r, mcp_for(wf_env, runner_for(r)) as api:
        result = await api.query(sql, "do it")
        action_id = only_action(wf_env, api)
        detail = await api.client.get(f"/v1/actions/{action_id}")
        assert detail.status_code == 401  # the dashboard API needs a user token, not the agent's
    assert status_history(wf_env, action_id) == history
    assert action_row(wf_env, action_id)["status"] == result["status"]


async def test_a_planted_email_reaches_no_log_line(
    wf_env: WorkflowEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    sql = f"INSERT INTO customer_notes (user_id, body) VALUES (42, '{PLANTED_EMAIL}')"
    async with rig(wf_env) as r, mcp_for(wf_env, runner_for(r)) as api:
        await api.query(sql, f"note for {PLANTED_EMAIL}")
        await api.query(f"SELECT '{PLANTED_EMAIL}'", f"echo {PLANTED_EMAIL}")
        await api.query(f"DELETE FROM users WHERE email = '{PLANTED_EMAIL}'", PLANTED_EMAIL)
    assert PLANTED_EMAIL not in capsys.readouterr().out
