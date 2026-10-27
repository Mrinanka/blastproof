"""Step 16: the guards around the commit: which database it opens, which shapes it refuses, how
errors and timeouts end, the fast lane, row hashing and what the logs hold."""

import asyncio
import time
import uuid
from typing import Any

import asyncpg
import pytest

from app.logging import configure_logging
from app.services import company_db
from tests.conftest import CommitCompany
from tests.workflow_helpers import (
    FAST_LANE_SQL,
    PLANTED_EMAIL,
    WorkflowEnv,
    action_row,
    approve,
    checkpoint_text,
    company_rig,
    events,
    new_action,
    review_action,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

DELETE_NINE = "DELETE FROM users WHERE id BETWEEN 11 AND 19"
NEON_URL = "postgresql://neon_user:neon_password@neon-host.invalid:5432/neondb"


def event_data(env: WorkflowEnv, action_id: uuid.UUID, kind: str) -> list[dict[str, Any]]:
    return [data for event, data in events(env, action_id) if event == kind]


# --- Which database ------------------------------------------------------------------------------


async def test_a_local_action_commits_on_the_local_url_even_when_the_process_profile_is_neon(
    wf_env: WorkflowEnv, commit_company: CommitCompany, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    real = company_db.open_connection

    def spy(**kwargs: Any) -> Any:
        opened.append(str(kwargs.get("dsn", "")))
        return real(**kwargs)

    monkeypatch.setattr(company_db, "open_connection", spy)
    settings = {"company_connection_profile": "neon", "company_db_writer_url": NEON_URL}
    async with company_rig(wf_env, commit_company, settings=settings) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
    assert opened and not any("neon-host.invalid" in dsn for dsn in opened)
    assert any(commit_company.database in dsn for dsn in opened)


async def test_a_missing_local_writer_url_fails_and_never_falls_back_to_the_other_profile(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    # The Neon URL is set (and even reachable); the action's system is `local`, which has none.
    settings = {
        "local_company_writer_url": None,
        "company_db_writer_url": commit_company.writer_url,
    }
    async with company_rig(wf_env, commit_company, settings=settings) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "commit_not_configured"
    assert await commit_company.count("users") == 200
    assert await commit_company.count("blastproof.applied_actions") == 0


# --- Shapes --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # Two target tables.
        "WITH d AS (DELETE FROM customer_notes WHERE id = 1 RETURNING user_id)"
        " UPDATE users SET plan = 'pro' WHERE id IN (SELECT user_id FROM d)",
        # The write is inside a CTE, the statement is a SELECT.
        "WITH d AS (DELETE FROM customer_notes WHERE id = 1 RETURNING *) SELECT * FROM d",
    ],
)
async def test_a_shape_the_commit_cannot_check_is_refused_at_commit(
    wf_env: WorkflowEnv, commit_company: CommitCompany, sql: str
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, sql)
        await approve(r, action_id)

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "commit_unsupported"
    assert await commit_company.count("customer_notes") == 30
    assert await commit_company.count("blastproof.applied_actions") == 0


async def test_an_update_that_leaves_some_matched_rows_as_they_were_is_not_drift(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    await commit_company.execute("UPDATE users SET country = 'DE' WHERE id BETWEEN 21 AND 25")
    sql = "UPDATE users SET country = 'DE' WHERE id BETWEEN 21 AND 30"
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, sql)
        await approve(r, action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert event_data(wf_env, action_id, "drifted") == []
    assert event_data(wf_env, action_id, "committed")[0]["rows"] == 5  # five changed, five did not
    assert await commit_company.count("users", "country = 'DE'") == 10
    [(images,)] = wf_env.db.execute(
        "SELECT count(*) FROM before_images WHERE action_id = %s", (action_id,)
    )
    assert images == 5


# --- Errors and timeouts -------------------------------------------------------------------------


async def test_a_foreign_key_violation_at_commit_ends_with_its_sqlstate_and_applies_nothing(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, "UPDATE customer_notes SET user_id = 150 WHERE id = 1")
        await commit_company.execute("DELETE FROM users WHERE id = 150")
        await approve(r, action_id)

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "commit_statement_error_23503"
    assert await commit_company.count("customer_notes", "user_id = 1 AND id = 1") == 1
    assert await commit_company.count("blastproof.applied_actions") == 0


async def test_a_row_another_session_holds_ends_in_a_lock_timeout_after_about_three_seconds(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        # After the preview: a clone of the company database ends every session on it.
        holder = await asyncpg.connect(
            f"postgresql://company:company@localhost:5433/{commit_company.database}"
        )
        try:
            tx = holder.transaction()
            await tx.start()
            await holder.fetch("SELECT id FROM users WHERE id = 15 FOR UPDATE")
            started = time.monotonic()
            await approve(r, action_id)
            elapsed = time.monotonic() - started
            await tx.rollback()
        finally:
            await holder.close()

    row = action_row(wf_env, action_id)
    assert row["status"] == "failed" and row["failure_reason"] == "commit_lock_timeout"
    assert 2.5 < elapsed < 15
    assert await commit_company.count("users") == 200
    assert await commit_company.count("blastproof.applied_actions") == 0


# --- The fast lane -------------------------------------------------------------------------------


async def test_a_fast_lane_insert_commits_with_the_count_check(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async with company_rig(wf_env, commit_company) as r:
        action_id = new_action(wf_env, FAST_LANE_SQL)
        await r.run(action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("customer_notes") == 31
    assert event_data(wf_env, action_id, "committed") == [
        {"stub": False, "rows": 1, "already_applied": False}
    ]


async def test_a_fast_lane_insert_whose_expected_count_is_off_by_one_is_drift(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    async def raise_the_expected_count(stage: str, _tx: Any) -> None:
        if stage == "start":
            wf_env.db.execute(
                "UPDATE actions SET impact = jsonb_set(impact, '{table_changes,customer_notes,"
                "inserted}', '2') WHERE id = %s",
                (holder["id"],),
            )

    holder: dict[str, uuid.UUID] = {}
    async with company_rig(wf_env, commit_company, fault=raise_the_expected_count) as r:
        holder["id"] = action_id = new_action(wf_env, FAST_LANE_SQL)
        await r.run(action_id)

    assert await commit_company.count("customer_notes") == 30
    [drifted, *_] = event_data(wf_env, action_id, "drifted")
    assert drifted["reason"] == "inserts"
    assert drifted["counts"] == {"inserted_expected": 2, "inserted_returned": 1}


# --- Hashing and logs ----------------------------------------------------------------------------


async def test_a_database_default_time_zone_and_date_style_do_not_cause_drift(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    await commit_company.execute(
        f"ALTER DATABASE {commit_company.database} SET TimeZone = 'Asia/Tokyo'"
    )
    await commit_company.execute(
        f"ALTER DATABASE {commit_company.database} SET DateStyle = 'SQL, DMY'"
    )
    async with company_rig(wf_env, commit_company) as r:
        action_id = await review_action(r, DELETE_NINE)
        await approve(r, action_id)

    assert event_data(wf_env, action_id, "drifted") == []
    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191


async def test_a_planted_email_in_a_row_never_reaches_the_logs_or_the_checkpoint(
    wf_env: WorkflowEnv, commit_company: CommitCompany, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()  # onto this test's captured stdout
    await commit_company.execute(f"UPDATE users SET email = '{PLANTED_EMAIL}' WHERE id = 15")
    async with company_rig(wf_env, commit_company) as r:
        committed = await review_action(r, DELETE_NINE)
        await approve(r, committed)
        # And a drift, which logs counts.
        drifted = await review_action(r, "DELETE FROM users WHERE id BETWEEN 31 AND 39")
        await commit_company.execute(
            f"UPDATE users SET full_name = '{PLANTED_EMAIL}' WHERE id = 33"
        )
        await approve(r, drifted)

    out = capsys.readouterr()
    assert "commit_done" in out.out and "commit_drift" in out.out  # the log is captured at all
    for text in (out.out, out.err, checkpoint_text(wf_env, committed)):
        assert PLANTED_EMAIL not in text
    for kind in ("users", "customer_notes", "DELETE FROM"):
        assert kind not in "".join(line for line in out.out.splitlines() if "commit_" in line)
    assert action_row(wf_env, drifted)["status"] == "awaiting_review"
    # The row values do live in the before-images, which never leave the backend.
    [(held,)] = wf_env.db.execute(
        "SELECT count(*) FROM before_images WHERE action_id = %s AND row_before::text LIKE %s",
        (committed, f"%{PLANTED_EMAIL}%"),
    )
    assert held == 1


# --- The clone lock ------------------------------------------------------------------------------


async def test_a_commit_in_flight_keeps_a_clone_of_the_company_database_out(
    wf_env: WorkflowEnv, commit_company: CommitCompany
) -> None:
    """A clone ends every session on its source, so it must wait for the commit (BE-49, D-199)."""
    release = asyncio.Event()
    in_flight = asyncio.Event()

    async def hold_before_commit(stage: str, _tx: Any) -> None:
        if stage == "before_commit":
            in_flight.set()
            await release.wait()

    async with company_rig(wf_env, commit_company, fault=hold_before_commit) as r:
        action_id = await review_action(r, DELETE_NINE)
        commit = asyncio.create_task(approve(r, action_id))
        await in_flight.wait()
        client = commit_company.client()
        clone = asyncio.create_task(client.create_shadow(uuid.uuid4()))
        await asyncio.sleep(1.5)
        assert not clone.done()  # waiting on the shared lock the commit holds
        release.set()
        await commit
        target = await asyncio.wait_for(clone, timeout=30)
        await client.delete_shadow(target)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert await commit_company.count("users") == 191
