"""Pinned literals mean the same thing to Postgres as the functions they replace (D-122).

Reads only, as the reader role on the local company database.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest

from app.services.classifier import classify
from tests.conftest import COMPANY_READER_URL

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

FROZEN = datetime(2026, 10, 1, 13, 5, 9, 123456, tzinfo=UTC)

# Every spelling, with a precision where Postgres accepts one.
EXPRESSIONS = [
    "now()",
    "current_timestamp",
    "current_timestamp(3)",
    "current_date",
    "localtimestamp",
    "localtimestamp(2)",
    "current_time",
    "current_time(1)",
    "localtime",
    "localtime(4)",
    "statement_timestamp()",
    "clock_timestamp()",
    "transaction_timestamp()",
    "pg_catalog.now()",
]


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(COMPANY_READER_URL, server_settings={"TimeZone": "UTC"})
    try:
        yield connection
    finally:
        await connection.close()


@pytest.mark.parametrize("expression", EXPRESSIONS)
async def test_the_pinned_literal_has_the_same_type(
    conn: asyncpg.Connection, expression: str
) -> None:
    pinned = classify(f"SELECT {expression}", now=FROZEN).pinned_sql
    assert pinned != f"SELECT {expression}"
    original_type = await conn.fetchval(f"SELECT pg_typeof({expression})::text")
    pinned_type = await conn.fetchval(f"SELECT pg_typeof(({pinned.removeprefix('SELECT ')}))::text")
    assert pinned_type == original_type


async def test_the_pinned_values_are_the_frozen_instant(conn: asyncpg.Connection) -> None:
    sql = classify(
        "SELECT now(), current_date, localtimestamp, current_time, localtime", now=FROZEN
    ).pinned_sql
    row = await conn.fetchrow(sql)
    assert row is not None
    assert row[0] == FROZEN
    assert row[1] == FROZEN.date()
    assert row[2] == FROZEN.replace(tzinfo=None)
    assert row[3] == FROZEN.timetz()
    assert row[4] == FROZEN.time()


async def test_the_pinned_replit_delete_is_valid_sql(conn: asyncpg.Connection) -> None:
    pinned = classify(
        "SELECT count(*) FROM users WHERE last_login_at < now() - interval '6 months'",
        now=datetime(2026, 10, 1, tzinfo=UTC),
    ).pinned_sql
    original = await conn.fetchval(
        "SELECT count(*) FROM users WHERE last_login_at < '2026-10-01T00:00:00Z'::timestamptz"
        " - interval '6 months'"
    )
    assert await conn.fetchval(pinned) == original
