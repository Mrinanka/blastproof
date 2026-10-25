"""BE-49: reads of company_main and clones of it do not harm each other (local profile)."""

import asyncio
import time
import uuid

import pytest

from app.config import Settings
from app.services import company_db
from tests.conftest import COMPANY_READER_URL, fake_neon_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

SETTINGS = Settings(
    _env_file=None,
    app_env="test",
    company_connection_profile="local",
    local_company_reader_url=COMPANY_READER_URL,
)


async def slow_read(seconds: float, started: asyncio.Event | None = None) -> float:
    async with company_db.connect_reader(SETTINGS) as conn:
        if started:
            started.set()
        await conn.fetchval(f"SELECT pg_sleep({seconds})")
        assert await conn.fetchval("SELECT count(*) FROM users") > 0
    return time.monotonic()


async def test_a_slow_read_finishes_unharmed_and_the_clone_waits() -> None:
    started = asyncio.Event()
    read = asyncio.create_task(slow_read(2.0, started))
    await started.wait()
    client = fake_neon_client()
    target = await client.create_shadow(uuid.uuid4())
    cloned_at = time.monotonic()
    try:
        read_finished_at = await read  # would raise if the clone had killed the connection
        assert cloned_at >= read_finished_at - 0.05
    finally:
        await client.delete_shadow(target)


async def test_two_reads_do_not_block_each_other() -> None:
    begin = time.monotonic()
    await asyncio.gather(slow_read(1.5), slow_read(1.5))
    assert time.monotonic() - begin < 2.8


async def test_the_neon_profile_takes_no_lock() -> None:
    neon = Settings(_env_file=None, app_env="test", company_db_reader_url=COMPANY_READER_URL)
    # Would wait forever if either of these took the exclusive lock.
    async with (
        company_db.company_main_lock(neon, shared=False),
        company_db.company_main_lock(neon, shared=False),
    ):
        pass
