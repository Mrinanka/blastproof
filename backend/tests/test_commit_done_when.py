"""Step 16, DW-B16: an approved write is applied exactly as previewed, or not at all.

Real graph, real `ShadowRunner` on `FakeNeonClient`, real `CompanyCommitter`, on a throwaway company
database of about 200 users (`commit_company`). Models, retrieval and the judge are fakes.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.core.clock import FrozenClock
from app.services import company_db
from tests.conftest import CommitCompany
from tests.workflow_helpers import (
    Rig,
    WorkflowEnv,
    action_row,
    approve,
    checkpoint_count,
    company_rig,
    decider_for,
    events,
    new_action,
    review_action,
    runner_for,
    status_history,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

# Nine free users with orders (27), order items (54), invoices (SET NULL, 9) and notes (9).
DELETE_NINE = "DELETE FROM users WHERE id BETWEEN 11 AND 19"
# At most ten rows, so the "small insert" rule allows it: nothing asks a person, until a drift.
SMALL_INSERT_SELECT = (
    "INSERT INTO products (sku, name, price_cents, active, created_at)"
    " SELECT 'sku-' || id, 'Item', 100, true, '2026-01-01T00:00:00Z' FROM users WHERE id <= 5"
)
LATE_USER = (
    "INSERT INTO users (email, full_name, country, plan, created_at, last_login_at)"
    " VALUES ('late@example.com', 'Late', 'US', 'free', now(), now())"
)


def before_images(env: WorkflowEnv, action_id: uuid.UUID) -> list[tuple[str, Any, Any, Any]]:
    return env.db.execute(
        "SELECT table_name, primary_key, row_before, row_after FROM before_images"
        " WHERE action_id = %s ORDER BY (primary_key ->> 'id')::bigint",
        (action_id,),
    )


def event_data(env: WorkflowEnv, action_id: uuid.UUID, kind: str) -> list[dict[str, Any]]:
    return [data for event, data in events(env, action_id) if event == kind]


async def review_action_or_run(r: Rig, sql: str) -> uuid.UUID:
    """A write the rules allow runs straight through (to a review, after a drift)."""
    action_id = new_action(r.env, sql)
    await r.run(action_id)
    return action_id


# --- The happy path ------------------------------------------------------------------------------


async def test_an_approved_delete_is_applied_exactly_as_previewed(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    before = await commit_company.fetch(
        "SELECT to_jsonb(u) AS row FROM users u WHERE id BETWEEN 11 AND 19 ORDER BY id"
    )
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    assert await commit_company.count("users") == 191
    assert await commit_company.count("users", "id BETWEEN 11 AND 19") == 0
    assert await commit_company.count("orders", "user_id BETWEEN 11 AND 19") == 0
    assert await commit_company.count("order_items") == 300 - 54
    assert await commit_company.count("invoices", "user_id IS NULL") == 9
    assert await commit_company.count("customer_notes") == 30 - 9
    assert await commit_company.count("blastproof.applied_actions") == 1

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert status_history(wf_env, action_id)[-3:] == ["approved", "committing", "committed"]
    assert event_data(wf_env, action_id, "committed") == [
        {"stub": False, "rows": 9, "already_applied": False}
    ]
    # The before-images are the target's deleted rows as they were, and nothing else (BE-96).
    images = before_images(wf_env, action_id)
    assert [table for table, *_ in images] == ["users"] * 9
    assert [row for *_, row, _after in images] == [json.loads(b["row"]) for b in before]
    assert all(after is None for *_, after in images)


async def test_an_allowed_insert_that_is_not_in_the_fast_lane_commits_with_its_count_check(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action_or_run(r, SMALL_INSERT_SELECT)
    assert await commit_company.count("products") == 5
    assert action_row(wf_env, action_id)["status"] == "committed"
    assert event_data(wf_env, action_id, "committed")[0]["rows"] == 5
    images = before_images(wf_env, action_id)
    assert len(images) == 5 and all(before is None and after for *_, before, after in images)


# --- Drift ---------------------------------------------------------------------------------------


async def test_a_row_changed_after_the_preview_is_previewed_again_and_goes_back_to_review(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        await commit_company.execute("UPDATE users SET country = 'DE' WHERE id = 15")
        await approve(r, action_id)

        # Nothing was applied; the write waits for a person again, on the same thread.
        assert await commit_company.count("users") == 200
        assert await commit_company.count("blastproof.applied_actions") == 0
        row = action_row(wf_env, action_id)
        assert row["status"] == "awaiting_review" and row["decision"] == "review"
        assert row["decided_by"] is None and row["decided_at"] is None
        assert before_images(wf_env, action_id) == []
        assert event_data(wf_env, action_id, "drifted") == [
            {"attempt": 1, "reason": "rows", "counts": {"rows_changed": 1}}
        ]
        assert await r.next_nodes(action_id) == ("human_review",)

        await approve(r, action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert status_history(wf_env, action_id).count("awaiting_review") == 2
    assert status_history(wf_env, action_id) == [
        "received",
        "classified",
        "previewing",
        "awaiting_review",
        "approved",
        "committing",
        "drifted",
        "previewing",
        "awaiting_review",
        "approved",
        "committing",
        "committed",
    ]


async def test_after_a_drift_a_write_the_rules_allow_is_not_approved_without_a_person(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async def delete_a_source_row(stage: str, _tx: Any) -> None:
        if stage == "start":
            await commit_company.execute("DELETE FROM users WHERE id = 3")

    async with company_rig(wf_env, commit_company, fault=delete_a_source_row) as r:
        action_id = await review_action_or_run(r, SMALL_INSERT_SELECT)
        row = action_row(wf_env, action_id)

        assert await commit_company.count("products") == 0
        assert row["status"] == "awaiting_review"
        assert row["decision"] == "review"
        assert event_data(wf_env, action_id, "drifted") == [
            {
                "attempt": 1,
                "reason": "inserts",
                "counts": {"inserted_expected": 5, "inserted_returned": 4},
            }
        ]
        # The judge and the rules still said allow; the drift is what asked for a person.
        decided = event_data(wf_env, action_id, "decided")
        assert [d["decision"] for d in decided] == ["allow", "review"]
        assert decided[-1]["deciding"] == "drift"

        await approve(r, action_id)

    assert await commit_company.count("products") == 4  # the new preview, as approved
    assert action_row(wf_env, action_id)["status"] == "committed"


async def test_a_new_row_that_matches_the_where_is_drift_in_the_key_set(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    sql = "DELETE FROM users WHERE id BETWEEN 11 AND 19 OR email LIKE 'late%'"
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, sql)
        await commit_company.execute(LATE_USER)
        await approve(r, action_id)

        assert await commit_company.count("users") == 201
        assert event_data(wf_env, action_id, "drifted") == [
            {"attempt": 1, "reason": "keys", "counts": {"deleted_extra": 1}}
        ]
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_a_child_row_that_was_nulled_after_the_preview_is_drift_in_the_cascade(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        # The invoice of user 12 no longer hangs off the user: the SET NULL path counts 8, not 9.
        await commit_company.execute("UPDATE invoices SET user_id = NULL WHERE user_id = 12")
        await approve(r, action_id)

        assert await commit_company.count("users") == 200
        assert event_data(wf_env, action_id, "drifted") == [
            {"attempt": 1, "reason": "cascades", "counts": {"cascade_paths_changed": 1}}
        ]


async def test_a_second_drift_fails_the_write_and_changes_nothing(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        await commit_company.execute("UPDATE users SET country = 'DE' WHERE id = 15")
        await approve(r, action_id)
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"
        await commit_company.execute("UPDATE users SET country = 'FR' WHERE id = 16")
        await approve(r, action_id)

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "drifted"
    assert row["decision"] == "block"
    assert await commit_company.count("users") == 200
    assert await commit_company.count("blastproof.applied_actions") == 0
    assert before_images(wf_env, action_id) == []
    assert [d["attempt"] for d in event_data(wf_env, action_id, "drifted")] == [1, 2]
    assert status_history(wf_env, action_id)[-2:] == ["drifted", "failed"]


# --- Committing twice ----------------------------------------------------------------------------


async def test_committing_twice_applies_once_and_the_second_says_so(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)
        again = await r.deps.committer.commit(action_id)

    assert again.already_applied and again.rows == 9 and not again.stub
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert len(before_images(wf_env, action_id)) == 9  # kept, not replaced or deleted


async def test_two_commits_at_once_apply_once(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        outcomes = await asyncio.gather(
            r.deps.committer.commit(action_id), r.deps.committer.commit(action_id)
        )

    assert sorted(o.already_applied for o in outcomes) == [False, True]
    assert {o.rows for o in outcomes} == {9}
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert len(before_images(wf_env, action_id)) == 9


# --- Crashes and dropped connections -------------------------------------------------------------


class Kill:
    """Stops the commit at `stage` the way a shutdown does: the task is cancelled while it waits."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.reached = asyncio.Event()

    async def __call__(self, stage: str, _tx: Any) -> None:
        if stage == self.stage:
            self.reached.set()
            await asyncio.Event().wait()  # until cancelled

    async def approve(self, r: Rig, action_id: uuid.UUID) -> None:
        task = asyncio.create_task(approve(r, action_id))
        await self.reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_kill_after_the_company_commit_is_settled_by_recovery_without_a_second_apply(
    fresh_wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    env = fresh_wf_env
    kill = Kill("after_commit")
    async with company_rig(env, commit_company, fault=kill) as r:
        action_id = await review_action(r, DELETE_NINE)
        await kill.approve(r, action_id)
        assert action_row(env, action_id)["status"] == "committing"
        assert await r.next_nodes(action_id) == ("commit",)
    assert await commit_company.count("users") == 191  # the write happened

    async with company_rig(env, commit_company) as r2:
        runner = runner_for(r2, recovered=False)
        report = await runner.recover()
        await runner.join()

    assert report is not None and report.reconciled == 1
    row = action_row(env, action_id)
    assert row["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert len(before_images(env, action_id)) == 9
    assert event_data(env, action_id, "committed") == [
        {"stub": False, "rows": 9, "already_applied": True}
    ]


async def test_a_kill_before_the_company_commit_is_rerun_by_recovery_and_applies_once(
    fresh_wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    env = fresh_wf_env
    kill = Kill("before_commit")
    async with company_rig(env, commit_company, fault=kill) as r:
        action_id = await review_action(r, DELETE_NINE)
        await kill.approve(r, action_id)
        assert action_row(env, action_id)["status"] == "committing"
    # The connection closed with the transaction open: nothing was applied, no marker.
    assert await commit_company.count("users") == 200
    assert await commit_company.count("blastproof.applied_actions") == 0
    assert len(before_images(env, action_id)) == 9  # saved before the commit was reached

    async with company_rig(env, commit_company) as r2:
        runner = runner_for(r2, recovered=False)
        await runner.recover()
        await runner.join()

    assert action_row(env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert len(before_images(env, action_id)) == 9  # replaced, not doubled
    assert event_data(env, action_id, "committed")[0]["already_applied"] is False


async def test_a_dropped_commit_connection_is_settled_from_the_marker_inline(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async def drop_after_commit(stage: str, _tx: Any) -> None:
        if stage == "after_commit":
            raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")

    async with company_rig(wf_env, commit_company, fault=drop_after_commit) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert event_data(wf_env, action_id, "committed") == [
        {"stub": False, "rows": 9, "already_applied": False}
    ]


async def test_a_dropped_commit_whose_marker_cannot_be_checked_stays_committing_until_the_sweep(
    fresh_wf_env: WorkflowEnv, commit_company: CommitCompany, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = fresh_wf_env

    async def lose_connection(stage: str, _tx: Any) -> None:
        if stage == "after_commit":

            def unreachable(**_: object) -> Any:
                raise company_db.CompanyDbUnavailableError("OSError")

            monkeypatch.setattr(company_db, "open_connection", unreachable)
            raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")

    async with company_rig(env, commit_company, fault=lose_connection) as r:
        action_id = await review_action(r, DELETE_NINE)
        from app.services.commit import CommitOutcomeUnknownError

        with pytest.raises(CommitOutcomeUnknownError):
            await approve(r, action_id)
        row = action_row(env, action_id)
        assert row["status"] == "committing" and row["failure_reason"] is None
        assert await r.next_nodes(action_id) == ("commit",)
        monkeypatch.undo()
        assert await commit_company.count("users") == 191  # it did happen

        # Not yet five minutes old: the sweep leaves it. Later it settles it from the marker.
        young = runner_for(r)
        await young.sweep()
        await young.join()
        assert action_row(env, action_id)["status"] == "committing"

        later = FrozenClock(datetime.now(UTC) + timedelta(minutes=6))
        runner = runner_for(r, clock=later)
        report = await runner.sweep()
        await runner.join()

    assert report is not None and report.reconciled == 1
    assert action_row(env, action_id)["status"] == "committed"
    assert await commit_company.count("blastproof.applied_actions") == 1
    assert event_data(env, action_id, "committed")[0]["already_applied"] is True


async def test_a_postgres_error_at_commit_fails_the_write_and_removes_the_before_images(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async def refuse_commit(stage: str, tx: Any) -> None:
        if stage == "before_commit":
            await tx.rollback()
            raise asyncpg.exceptions.SerializationError("could not serialize")

    async with company_rig(wf_env, commit_company, fault=refuse_commit) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "commit_error_40001"
    assert before_images(wf_env, action_id) == []
    assert await commit_company.count("users") == 200
    assert await commit_company.count("blastproof.applied_actions") == 0


# --- A restart while a person decides ------------------------------------------------------------


async def test_a_restart_while_awaiting_review_still_commits_when_a_new_process_approves(
    fresh_wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    env = fresh_wf_env
    async with company_rig(env, commit_company) as first:
        action_id = await review_action(first, DELETE_NINE)
        assert checkpoint_count(env, action_id) > 0

    async with company_rig(env, commit_company) as second:  # the backend restarted
        runner = runner_for(second)
        await runner.resume(action_id, approved=True, note=None, decider=decider_for(env.user))
        await runner.join()

    assert action_row(env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert await commit_company.count("blastproof.applied_actions") == 1
