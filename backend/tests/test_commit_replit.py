"""Step 16, DW-B16 on the real seed: the Replit delete goes review -> approve -> commit with the
exact `incidents.yaml` counts. Slow tier: it clones the 273k-row seed twice. It commits to a scratch
clone of `company_template`; `company_main` is never written."""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.core.clock import FrozenClock
from tests.conftest import (
    COMPANY_WRITER_URL,
    CommitCompany,
    company_url,
    leftover_shadow_databases,
    on_database,
    replit_impact,
)
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL
from tests.test_shadow_done_when import TOTAL_CHANGED_ROWS
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    approve,
    company_rig,
    events,
    review_action,
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
