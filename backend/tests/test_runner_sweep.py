"""`WorkflowRunner.sweep` and `maintain`: expiry, stuck actions and retention, each in a scratch
database of its own (the sweep acts on every action in the database)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.core.clock import FrozenClock
from app.workflow.runner import RUNNER_LOCK_KEY
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    events,
    new_action,
    paused_action,
    paused_rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]
SQL = "DELETE FROM orders WHERE id > 0"


def in_status(env: WorkflowEnv, status: str, entered: datetime) -> uuid.UUID:
    """An action that entered `status` at `entered` (its event is stamped with that time)."""
    action_id = new_action(env, SQL)
    env.db.execute("UPDATE actions SET status = %s WHERE id = %s", (status, action_id))
    env.db.execute(
        "INSERT INTO audit_events (action_id, event_type, created_at) VALUES (%s, %s, %s)",
        (action_id, status, entered),
    )
    return action_id


def db_now(env: WorkflowEnv) -> datetime:
    [(now,)] = env.db.execute("SELECT now()")
    return now


async def test_it_expires_an_overdue_review_and_leaves_one_not_due(
    fresh_wf_env: WorkflowEnv,
) -> None:
    env = fresh_wf_env
    async with paused_rig(env) as r:
        overdue, not_due = await paused_action(r), await paused_action(r)
        due_at = action_row(env, overdue)["expires_at"]
        env.db.execute(
            "UPDATE actions SET expires_at = %s WHERE id = %s",
            (due_at + timedelta(hours=100), not_due),
        )
        runner = runner_for(r, clock=FrozenClock(due_at + timedelta(seconds=1)))
        report = await runner.sweep()
    assert report is not None and report.expired == 1
    row = action_row(env, overdue)
    assert row["status"] == "expired" and row["decided_by"] is None
    assert row["decided_at"] is not None
    assert action_row(env, not_due)["status"] == "awaiting_review"
    assert ("expired", {"reason": "review_timeout"}) in events(env, overdue)


async def test_it_fails_stuck_actions_after_five_minutes_only(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    entered = db_now(env)
    stuck = {
        s: in_status(env, s, entered) for s in ("received", "classified", "previewing", "drifted")
    }
    async with paused_rig(env) as r:
        early = runner_for(r, clock=FrozenClock(entered + timedelta(minutes=4, seconds=59)))
        report = await early.sweep()
        assert report is not None and report.failed_stuck == 0
        late = runner_for(r, clock=FrozenClock(entered + timedelta(minutes=5, seconds=1)))
        report = await late.sweep()
    assert report is not None and report.failed_stuck == 4
    for action_id in stuck.values():
        row = action_row(env, action_id)
        assert (row["status"], row["failure_reason"], row["decision"]) == (
            "failed",
            "stuck",
            "block",
        )


async def test_stuck_time_runs_from_the_latest_event_of_the_status(
    fresh_wf_env: WorkflowEnv,
) -> None:
    """A re-preview after drift re-enters `previewing` hours later and is not failed early."""
    env = fresh_wf_env
    first = db_now(env)
    action_id = in_status(env, "previewing", first)
    again = first + timedelta(hours=3)
    env.db.execute(
        "INSERT INTO audit_events (action_id, event_type, created_at)"
        " VALUES (%s, 'previewing', %s)",
        (action_id, again),
    )
    async with paused_rig(env) as r:
        runner = runner_for(r, clock=FrozenClock(again + timedelta(minutes=4)))
        await runner.sweep()
        assert action_row(env, action_id)["status"] == "previewing"
        runner = runner_for(r, clock=FrozenClock(again + timedelta(minutes=6)))
        await runner.sweep()
    assert action_row(env, action_id)["status"] == "failed"


async def test_retention_deletes_old_working_data_and_keeps_audit_events(
    fresh_wf_env: WorkflowEnv,
) -> None:
    env = fresh_wf_env
    now = datetime.now(UTC)
    old, new = now - timedelta(days=31), now - timedelta(days=1)
    action_id = new_action(env, SQL)
    inserts = {
        "login_attempts": (
            "INSERT INTO login_attempts (email, ip, succeeded, created_at)"
            " VALUES ('a@example.com', '10.0.0.1', true, %s)"
        ),
        "hook_events": (
            "INSERT INTO hook_events (user_id, agent_id, tool, summary, decision, reason,"
            f" created_at) VALUES ('{env.user.id}', '{env.agent_id}', 'Bash', 's', 'allow',"
            " 'r', %s)"
        ),
        "before_images": (
            "INSERT INTO before_images (action_id, table_name, primary_key, created_at)"
            f" VALUES ('{action_id}', 'orders', '{{\"id\": 1}}', %s)"
        ),
        "preview_rows": (
            "INSERT INTO preview_rows (action_id, table_name, primary_key, change, created_at)"
            f" VALUES ('{action_id}', 'orders', '{{\"id\": 1}}', 'deleted', %s)"
        ),
    }
    for sql in inserts.values():
        env.db.execute(sql, (old,))
        env.db.execute(
            sql.replace("'a@example.com'", "'b@example.com'").replace('"id": 1', '"id": 2'), (new,)
        )
    env.db.execute(
        "INSERT INTO audit_events (action_id, event_type, created_at) VALUES (%s, 'decided', %s)",
        (action_id, old),
    )
    [(audit_before,)] = env.db.execute("SELECT count(*) FROM audit_events")
    async with paused_rig(env) as r:
        report = await runner_for(r, clock=FrozenClock(now)).sweep()
    assert report is not None and report.deleted == dict.fromkeys(inserts, 1) | {}
    for table in inserts:
        [(left,)] = env.db.execute(f"SELECT count(*) FROM {table}")
        assert left == 1
    assert env.db.execute("SELECT count(*) FROM audit_events") == [(audit_before,)]


async def test_a_held_lock_means_none_and_no_change(fresh_wf_env: WorkflowEnv) -> None:
    env = fresh_wf_env
    entered = db_now(env)
    action_id = in_status(env, "previewing", entered)
    with psycopg.connect(env.db.app_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (RUNNER_LOCK_KEY,))
        async with paused_rig(env) as r:
            runner = runner_for(r, clock=FrozenClock(entered + timedelta(hours=1)))
            assert await runner.sweep() is None
    assert action_row(env, action_id)["status"] == "previewing"


async def test_a_raising_sweep_is_logged_and_the_loop_goes_on(
    fresh_wf_env: WorkflowEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with paused_rig(fresh_wf_env) as r:
        runner = runner_for(r)
        calls = 0

        async def flaky() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("planted.secret@example.com")

        sleeps = 0

        async def sleep(_: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps == 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(runner, "sweep", flaky)
        monkeypatch.setattr(runner, "_sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await runner.maintain()
        assert calls == 3
