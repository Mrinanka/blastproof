"""One run of the real graph with the real `ShadowRunner` on `FakeNeonClient`: the Replit delete.

Only the models and the knowledge service are fakes. The write is reviewed with the exact
`incidents.yaml` counts stored in `actions.impact`, then approved and committed through the stub;
`company_main` is never touched.
"""

import asyncpg
import pytest

from app.core.clock import FrozenClock
from tests.conftest import (
    COMPANY_READER_URL,
    fake_neon_client,
    leftover_shadow_databases,
    replit_impact,
    shadow_runner,
)
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL
from tests.test_shadow_done_when import TOTAL_CHANGED_ROWS
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    claim,
    event_types,
    new_action,
    rig,
    status_history,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]


async def count_users() -> int:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        return await conn.fetchval("SELECT count(*) FROM users")
    finally:
        await conn.close()


async def test_the_replit_delete_end_to_end(wf_env: WorkflowEnv, frozen_clock: FrozenClock) -> None:
    runner = shadow_runner({"local": fake_neon_client(frozen_clock)})
    users_before = await count_users()
    async with rig(wf_env, real_shadow=runner, clock=frozen_clock) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        expected = replit_impact()
        assert row["status"] == "awaiting_review"
        assert row["decision"] == "review"
        assert row["impact"]["table_changes"] == {
            table: {
                "deleted": change.deleted,
                "updated": change.updated,
                "inserted": change.inserted,
            }
            for table, change in expected.table_changes.items()
        }
        assert row["impact"]["breakdowns"] == expected.breakdowns
        assert row["impact"]["derived_groups"] == expected.derived_groups
        [(preview_rows,)] = wf_env.db.execute(
            "SELECT count(*) FROM preview_rows WHERE action_id = %s", (action_id,)
        )
        assert preview_rows == TOTAL_CHANGED_ROWS
        assert row["pinned_sql"].startswith("DELETE FROM users WHERE last_login_at <")
        assert "now()" not in row["pinned_sql"]  # the clock is pinned (D-122)

        claim(wf_env, action_id, approved=True)
        await r.resume(action_id, approved=True)

        assert action_row(wf_env, action_id)["status"] == "committed"
        assert r.committer.committed == [action_id]  # the stub: nothing was applied
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "awaiting_review",
            "approved",
            "committing",
            "committed",
        ]
        assert "shadow_done" in event_types(wf_env, action_id)

    assert await count_users() == users_before  # company_main is untouched
    assert await leftover_shadow_databases() == []
