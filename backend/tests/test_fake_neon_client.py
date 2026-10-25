"""Step 10: FakeNeonClient, the local stand-in for Neon branches (BE-48, TEST-04)."""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FrozenClock
from app.services.neon import FakeNeonClient
from tests.conftest import (
    COMPANY_ADMIN_URL,
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    company_url,
    fake_neon_client,
    leftover_shadow_databases,
    on_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def admin() -> asyncpg.Connection:
    return await asyncpg.connect(company_url("postgres"))


async def comment_of(name: str) -> str | None:
    conn = await admin()
    try:
        return await conn.fetchval(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1",
            name,
        )
    finally:
        await conn.close()


async def test_a_clone_has_the_data_the_rights_and_a_creation_time() -> None:
    client = fake_neon_client(FrozenClock(NOW))
    target = await client.create_shadow(uuid.uuid4())
    try:
        assert target.branch_id.startswith("shadow_")
        assert await comment_of(target.branch_id) == "bp-shadow 2026-10-01T12:00:00Z"
        # CREATE DATABASE does not copy database rights (Known issue), so the client grants them.
        writer = await asyncpg.connect(on_database(COMPANY_WRITER_URL, target.branch_id))
        reader = await asyncpg.connect(on_database(COMPANY_READER_URL, target.branch_id))
        try:
            assert await reader.fetchval("SELECT count(*) FROM users") > 40_000
            await writer.execute("CREATE TEMP TABLE scratch (a int)")  # TEMPORARY is granted
            await writer.execute("DELETE FROM feature_flags WHERE id = 1")
        finally:
            await writer.close()
            await reader.close()
    finally:
        await client.delete_shadow(target)
    assert await leftover_shadow_databases() == []


async def test_a_write_in_the_clone_does_not_reach_company_main() -> None:
    client = fake_neon_client()
    target = await client.create_shadow(uuid.uuid4())
    try:
        writer = await asyncpg.connect(on_database(COMPANY_WRITER_URL, target.branch_id))
        try:
            await writer.execute("DELETE FROM feature_flags")
        finally:
            await writer.close()
    finally:
        await client.delete_shadow(target)
    reader = await asyncpg.connect(COMPANY_READER_URL)
    try:
        assert await reader.fetchval("SELECT count(*) FROM feature_flags") > 0
    finally:
        await reader.close()


async def test_delete_is_idempotent() -> None:
    client = fake_neon_client()
    target = await client.create_shadow(uuid.uuid4())
    await client.delete_shadow(target)
    await client.delete_shadow(target)
    assert await leftover_shadow_databases() == []


async def test_sweep_removes_old_and_comment_less_copies_and_keeps_fresh_ones() -> None:
    names = {k: f"shadow_test_{k}_{uuid.uuid4().hex[:6]}" for k in ("old", "fresh", "bare", "junk")}
    stamps = {"old": NOW - timedelta(minutes=30), "fresh": NOW - timedelta(minutes=5)}
    conn = await admin()
    try:
        for name in names.values():
            await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        for key, stamp in stamps.items():
            await conn.execute(
                f"COMMENT ON DATABASE \"{names[key]}\" IS 'bp-shadow {stamp:%Y-%m-%dT%H:%M:%SZ}'"
            )
        await conn.execute(f"COMMENT ON DATABASE \"{names['junk']}\" IS 'not a stamp'")
        assert await fake_neon_client(FrozenClock(NOW)).sweep(timedelta(minutes=15)) == 3
        assert await leftover_shadow_databases() == [names["fresh"]]
    finally:
        for name in names.values():
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.close()


async def test_ping_reaches_the_server() -> None:
    await fake_neon_client().ping()


async def test_a_failed_clone_leaves_nothing_behind() -> None:
    client = FakeNeonClient(
        admin_url=COMPANY_ADMIN_URL,
        writer_url=COMPANY_WRITER_URL,
        source_db="no_such_database",
        clock=FrozenClock(NOW),
    )
    with pytest.raises(asyncpg.PostgresError):  # the template does not exist
        await client.create_shadow(uuid.uuid4())
    assert await leftover_shadow_databases() == []
