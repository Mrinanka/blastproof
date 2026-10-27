"""Step 17, NFR-05: about five small writes and five reads at once through MCP, on the real
`ShadowRunner` with `FakeNeonClient`. Slow tier: each preview copies the seeded company database.

No more than `MAX_CONCURRENT_SHADOWS` previews overlap, every action ends in its expected state,
and `company_main` is never written (the commit is the recording stub).
"""

import asyncio
from typing import Any

import asyncpg
import pytest

from app.core.clock import SystemClock
from app.services.neon import FakeNeonClient, ShadowTarget
from tests.conftest import (
    COMPANY_ADMIN_URL,
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    leftover_shadow_databases,
    shadow_runner,
)
from tests.mcp_write_helpers import action_ids, mcp_for
from tests.workflow_helpers import WorkflowEnv, action_row, rig, runner_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

MAX_SHADOWS = 2
WRITES = 5
READS = 5


class CountingNeon(FakeNeonClient):
    """Counts the shadow copies that exist at the same time."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.alive = 0
        self.peak = 0
        self.created = 0

    async def create_shadow(self, action_id: Any) -> ShadowTarget:
        target = await super().create_shadow(action_id)
        self.alive += 1
        self.created += 1
        self.peak = max(self.peak, self.alive)
        return target

    async def delete_shadow(self, target: ShadowTarget) -> None:
        await super().delete_shadow(target)
        self.alive -= 1


async def company_checksum() -> tuple[int, int]:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        row = await conn.fetchrow("SELECT count(*), coalesce(sum(price_cents), 0) FROM products")
        return row[0], int(row[1])
    finally:
        await conn.close()


async def test_five_writes_and_five_reads_at_once(wf_env: WorkflowEnv) -> None:
    neon = CountingNeon(
        admin_url=COMPANY_ADMIN_URL, writer_url=COMPANY_WRITER_URL, clock=SystemClock()
    )
    shadow = shadow_runner({"local": neon}, max_concurrent=MAX_SHADOWS, slot_wait_seconds=300)
    before = await company_checksum()
    settings = {"review_wait_seconds": 240, "max_concurrent_shadows": MAX_SHADOWS}
    async with (
        rig(wf_env, real_shadow=shadow, settings=settings) as r,
        mcp_for(wf_env, runner_for(r), **settings) as api,
    ):
        writes = [
            api.query(
                f"UPDATE products SET price_cents = price_cents + 1 WHERE id = {i + 1}",
                f"raise price {i}",
            )
            for i in range(WRITES)
        ]
        reads = [api.query(f"SELECT {i} AS n, count(*) FROM users") for i in range(READS)]
        results = await asyncio.gather(*writes, *reads)
        write_results, read_results = results[:WRITES], results[WRITES:]

        assert [x["status"] for x in read_results] == ["executed"] * READS
        statuses = {x["status"] for x in write_results}
        # A small update is judged the same way every time: all committed or all held.
        assert len(statuses) == 1 and statuses <= {"committed", "awaiting_review"}, write_results
        for result in write_results:
            assert action_row(wf_env, result["action_id"])["status"] == result["status"]
        assert len(action_ids(wf_env, api.agent)) == WRITES + READS
    assert neon.created == WRITES
    assert 1 <= neon.peak <= MAX_SHADOWS
    assert neon.alive == 0 and await leftover_shadow_databases() == []
    assert await company_checksum() == before  # only the stub committed: nothing was written
    committed = sum(x["status"] == "committed" for x in write_results)
    assert len(r.committer.committed) == committed
