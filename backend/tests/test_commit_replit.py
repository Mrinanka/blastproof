"""Step 16, DW-B16 on the real seed: the Replit delete goes review -> approve -> commit with the
exact `incidents.yaml` counts. Slow tier: it clones the 273k-row seed twice. It commits to a scratch
clone of `company_template`; `company_main` is never written.

Gate 1 (GATE-03) runs the same delete the way the demo does: through `/mcp`, the real preview, the
approve route that `/docs` calls and the real commit, in one chain."""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.core.clock import FrozenClock
from app.services import agent_messages
from tests.action_helpers import token_for
from tests.conftest import (
    COMPANY_WRITER_URL,
    CommitCompany,
    company_url,
    leftover_shadow_databases,
    on_database,
    replit_impact,
)
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL
from tests.mcp_write_helpers import mcp_for
from tests.test_shadow_done_when import TOTAL_CHANGED_ROWS
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    approve,
    company_rig,
    events,
    review_action,
    runner_for,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]


@pytest.fixture
async def seeded_clone() -> AsyncIterator[CommitCompany]:
    name = f"commit_replit_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(company_url("postgres"))
    try:
        await admin.execute(f"CREATE DATABASE {name} TEMPLATE company_template")
        await admin.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        await admin.execute(f"GRANT CONNECT ON DATABASE {name} TO blastproof_reader")
        await admin.execute(f"GRANT CONNECT, TEMPORARY ON DATABASE {name} TO blastproof_writer")
        yield CommitCompany(name, on_database(COMPANY_WRITER_URL, name))
    finally:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.close()


async def main_users() -> int:
    conn = await asyncpg.connect(company_url("company_main"))
    try:
        return await conn.fetchval("SELECT count(*) FROM users")
    finally:
        await conn.close()


async def test_the_replit_delete_commits_with_the_exact_counts(
    wf_env: WorkflowEnv, seeded_clone: CommitCompany, frozen_clock: FrozenClock
) -> None:
    expected = replit_impact()
    main_before = await main_users()
    tables = [t for t, change in expected.table_changes.items() if change.deleted]
    before = {table: await seeded_clone.count(table) for table in tables}
    async with company_rig(wf_env, seeded_clone, clock=frozen_clock) as r:
        action_id = await review_action(r, REPLIT_SQL, intent=REPLIT_INTENT)
        row = action_row(wf_env, action_id)
        assert row["decision"] == "review"
        [(preview_rows,)] = wf_env.db.execute(
            "SELECT count(*) FROM preview_rows WHERE action_id = %s", (action_id,)
        )
        assert preview_rows == TOTAL_CHANGED_ROWS
        await approve(r, action_id)

    assert action_row(wf_env, action_id)["status"] == "committed"
    assert expected.table_changes["users"].deleted == 48_213
    for table in tables:
        deleted = expected.table_changes[table].deleted
        assert await seeded_clone.count(table) == before[table] - deleted
    nulled = expected.table_changes["invoices"].updated
    assert await seeded_clone.count("invoices", "user_id IS NULL") >= nulled
    assert await seeded_clone.count("blastproof.applied_actions") == 1
    committed = [data for kind, data in events(wf_env, action_id) if kind == "committed"]
    assert committed == [{"stub": False, "rows": 48_213, "already_applied": False}]
    [(images,)] = wf_env.db.execute(
        "SELECT count(*) FROM before_images WHERE action_id = %s", (action_id,)
    )
    assert images == 48_213  # the target table only (BE-96)

    assert await main_users() == main_before  # company_main is untouched
    assert await leftover_shadow_databases() == []


async def test_gate_1_the_replit_delete_is_held_then_approved_over_http_and_committed(
    wf_env: WorkflowEnv, seeded_clone: CommitCompany, frozen_clock: FrozenClock
) -> None:
    """GATE-03: nothing in the chain is faked but the models and the judge."""
    expected = replit_impact()
    main_before = await main_users()
    users_before = await seeded_clone.count("users")
    # The clock is frozen at the anchor date, but the detail route checks `can_decide` against the
    # real clock: a year-long review window keeps the review open for both.
    window = {"review_timeout_hours": 24 * 365}
    async with company_rig(wf_env, seeded_clone, clock=frozen_clock, settings=window) as r:
        runner = runner_for(r)
        # A preview of the whole seed can pass the default 20 s wait; wait it out here.
        async with mcp_for(wf_env, runner, review_wait_seconds=120) as api:
            held = await api.query(REPLIT_SQL, REPLIT_INTENT)
            action_id = held["action_id"]
            assert held["status"] == "awaiting_review"
            assert held["message"] == agent_messages.pending(action_id)
            assert await r.next_nodes(uuid.UUID(action_id)) == ("human_review",)

            headers = token_for(wf_env.user)
            shown = (await api.client.get(f"/v1/actions/{action_id}", headers=headers)).json()
            assert shown["status"] == "awaiting_review" and shown["can_decide"] is True
            shown_deleted = {c["table"]: c["deleted"] for c in shown["table_changes"]}
            assert shown_deleted["users"] == expected.table_changes["users"].deleted

            response = await api.client.post(f"/v1/actions/{action_id}/approve", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "approved"
            await runner.join()

            done = await api.status(action_id)
            assert done["status"] == "committed"
            assert done["message"] == agent_messages.committed(48_213, "deleted", "users")

    row = action_row(wf_env, uuid.UUID(action_id))
    assert row["status"] == "committed"
    kinds = [kind for kind, _ in events(wf_env, uuid.UUID(action_id))]
    assert kinds[-4:] == ["awaiting_review", "approved", "committing", "committed"]
    committed = [data for kind, data in events(wf_env, uuid.UUID(action_id)) if kind == "committed"]
    assert committed == [{"stub": False, "rows": 48_213, "already_applied": False}]
    assert await seeded_clone.count("users") == users_before - 48_213
    assert await seeded_clone.count("blastproof.applied_actions") == 1
    assert await main_users() == main_before  # company_main is untouched
    assert await leftover_shadow_databases() == []
