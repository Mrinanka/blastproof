"""Step 10, D-136: a row's hash is the same on a shadow copy and on the real database.

The commit's drift check (step 16) compares hashes taken on `company_main` through `connect_writer`
with the ones the shadow run stored, so they must not depend on the session's settings.
"""

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import FrozenClock
from app.services import company_db
from app.services.classifier import classify
from app.services.company_schema import ROW_HASH_SQL
from app.services.neon import FakeNeonClient, ShadowTarget
from tests.conftest import (
    COMPANY_WRITER_URL,
    fake_neon_client,
    shadow_runner,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

CLOCK = FrozenClock(datetime(2026, 10, 1, tzinfo=UTC))
HASH_ONE_USER = f"SELECT {ROW_HASH_SQL} FROM users t WHERE id = $1"
SETTINGS = Settings(
    _env_file=None,
    app_env="test",
    company_connection_profile="local",
    local_company_writer_url=COMPANY_WRITER_URL,
)


class OddZoneClient(FakeNeonClient):
    """A clone whose database default TimeZone is not UTC (the hash must not care)."""

    zone = "Asia/Kolkata"

    async def create_shadow(self, action_id: uuid.UUID) -> ShadowTarget:
        target = await super().create_shadow(action_id)
        async with self._admin() as conn:
            await conn.execute(
                f"ALTER DATABASE \"{target.branch_id}\" SET TimeZone TO '{self.zone}'"
            )
            await conn.execute(f"ALTER DATABASE \"{target.branch_id}\" SET DateStyle TO 'German'")
        return target


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/Los_Angeles"])
async def test_a_shadow_hash_equals_the_hash_on_company_main(zone: str) -> None:
    client = fake_neon_client(CLOCK)
    odd = OddZoneClient(admin_url=client._admin_url, writer_url=client._writer_url, clock=CLOCK)
    odd.zone = zone
    classification = classify("DELETE FROM users WHERE id IN (1, 2, 3)", now=CLOCK.now())
    runner = shadow_runner({"local": odd})
    diff = (
        await runner.run(action_id=uuid.uuid4(), profile="local", classification=classification)
    ).diff

    users = [c for c in diff.changes if c.table == "users"]
    assert len(users) == 3
    async with company_db.connect_writer(SETTINGS) as conn:
        for change in users:
            assert change.before_hash == await conn.fetchval(
                HASH_ONE_USER, change.primary_key["id"]
            )


async def test_a_raw_connection_in_another_zone_hashes_differently() -> None:
    """Proves the test above can fail: without the pinned settings the hash moves."""
    async with company_db.connect_writer(SETTINGS) as conn:
        pinned = await conn.fetchval(HASH_ONE_USER, 1)
    raw = await asyncpg.connect(COMPANY_WRITER_URL, server_settings={"TimeZone": "Asia/Kolkata"})
    try:
        assert await raw.fetchval(HASH_ONE_USER, 1) != pinned
    finally:
        await raw.close()
