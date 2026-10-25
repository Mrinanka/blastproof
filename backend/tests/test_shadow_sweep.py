"""BE-58: leftover shadow copies are swept at startup, one backend instance at a time."""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FrozenClock
from app.db.session import make_engine
from app.services.neon import BRANCH_LIFETIME, SWEEP_LOCK_KEY, sweep_stale_shadows
from tests.conftest import (
    AuthDb,
    asyncpg_url,
    company_url,
    fake_neon_client,
    leftover_shadow_databases,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def make_stale_copy() -> str:
    name = f"shadow_test_stale_{uuid.uuid4().hex[:6]}"
    stamp = (NOW - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = await asyncpg.connect(company_url("postgres"))
    try:
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        await conn.execute(f"COMMENT ON DATABASE \"{name}\" IS 'bp-shadow {stamp}'")
    finally:
        await conn.close()
    return name


async def drop(name: str) -> None:
    conn = await asyncpg.connect(company_url("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def test_the_sweep_deletes_stale_copies(auth_db: AuthDb) -> None:
    name = await make_stale_copy()
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        clients = {"local": fake_neon_client(FrozenClock(NOW))}
        await sweep_stale_shadows(clients, engine, older_than=BRANCH_LIFETIME)
        assert name not in await leftover_shadow_databases()
    finally:
        await engine.dispose()
        await drop(name)


async def test_a_second_instance_skips_while_the_first_sweeps(auth_db: AuthDb) -> None:
    name = await make_stale_copy()
    engine = make_engine(asyncpg_url(auth_db.app_url))
    holder = await asyncpg.connect(auth_db.app_url)
    try:
        await holder.fetchval("SELECT pg_advisory_lock($1)", SWEEP_LOCK_KEY)
        clients = {"local": fake_neon_client(FrozenClock(NOW))}
        await sweep_stale_shadows(clients, engine, older_than=BRANCH_LIFETIME)
        assert name in await leftover_shadow_databases()  # skipped: the lock was taken
    finally:
        await holder.close()
        await engine.dispose()
        await drop(name)


async def test_a_failing_sweep_never_raises(auth_db: AuthDb) -> None:
    class Broken:
        async def sweep(self, older_than: timedelta) -> int:
            raise RuntimeError("neon is down")

    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        await sweep_stale_shadows({"neon": Broken()}, engine, older_than=BRANCH_LIFETIME)  # type: ignore[dict-item]
        await sweep_stale_shadows({"neon": Broken()}, None, older_than=BRANCH_LIFETIME)  # type: ignore[dict-item]
    finally:
        await engine.dispose()
