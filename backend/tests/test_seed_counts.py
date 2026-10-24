"""EVAL-03: the expected counts in the scenario files are true facts about the seed.

Each SQL scenario with `expected_impact` runs inside a transaction on a scratch copy of
company_template and is rolled back. `now()` is replaced by midnight UTC on the anchor date, as the
classifier's frozen Clock will pin it (step 8, D-017), so the counts are exact at any time of day.
Effects are measured the way the shadow run will (D-004): hash every row by primary key before and
after, and diff. This guards the numbers steps 8-13 build on; it does not test the pipeline.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest

from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import company_url

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "evals" / "scenarios"

# Mirrors rules.yaml's derived group (backend plan §9), which step 9 makes the single source.
DERIVED_GROUPS = {"users.paying": ("users.plan", {"pro", "enterprise"})}

# The time functions step 8 pins. Scenario SQL with expected counts may use only now().
TIME_FUNCTIONS = re.compile(
    r"current_timestamp|current_date|localtimestamp|statement_timestamp|clock_timestamp"
    r"|transaction_timestamp",
    re.IGNORECASE,
)


def pin_now(sql: str, anchor: str) -> str:
    assert not TIME_FUNCTIONS.search(sql), "pin only now() here; step 8 pins the rest"
    return re.sub(r"now\(\)", f"timestamptz '{anchor} 00:00:00+00'", sql, flags=re.IGNORECASE)


def measured_scenarios() -> list[tuple[SqlScenario, str]]:
    """(scenario, its file's anchor date) for every SQL scenario with expected counts."""
    scenarios = []
    for name in ("incidents.yaml", "labeled.yaml"):
        scenario_file = load_scenario_file(SCENARIOS_DIR / name)
        for s in scenario_file.scenarios:
            if isinstance(s, SqlScenario) and s.expected_impact is not None:
                scenarios.append((s, scenario_file.anchor_date.isoformat()))
    return scenarios


async def create_scratch_copy(name: str) -> None:
    admin = await asyncpg.connect(company_url("postgres"))
    try:
        await admin.execute(f"CREATE DATABASE {name} TEMPLATE company_template")
    finally:
        await admin.close()


async def drop_scratch_copy(name: str) -> None:
    admin = await asyncpg.connect(company_url("postgres"))
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def scratch_copy() -> Iterator[str]:
    """A scratch copy of the seed, so a failed rollback could never touch company_main."""
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    asyncio.run(create_scratch_copy(name))
    try:
        yield name
    finally:
        asyncio.run(drop_scratch_copy(name))


@pytest.fixture
async def conn(scratch_copy: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(
        company_url(scratch_copy), server_settings={"TimeZone": "UTC"}
    )
    yield connection
    await connection.close()


async def measure(
    conn: asyncpg.Connection, sql: str, breakdown_columns: set[str]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Run `sql` and roll back. Returns per-table changes and breakdowns of changed rows."""
    tables = [
        r["tablename"]
        for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    ]
    tr = conn.transaction()
    await tr.start()
    try:
        for t in tables:
            extra = "".join(
                f", {c.split('.')[1]}" for c in breakdown_columns if c.split(".")[0] == t
            )
            await conn.execute(
                f"CREATE TEMP TABLE before_{t} ON COMMIT DROP AS"
                f" SELECT id, md5(row_to_json(t)::text) AS h{extra} FROM {t} t"
            )
        await conn.execute(sql)
        changes: dict[str, dict[str, int]] = {}
        diff = (
            "FROM before_{t} b FULL OUTER JOIN (SELECT id, md5(row_to_json(t)::text) AS h"
            " FROM {t} t) a ON a.id = b.id WHERE b.h IS DISTINCT FROM a.h"
        )
        for t in tables:
            row = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE a.id IS NULL) AS deleted,"
                " count(*) FILTER (WHERE b.id IS NULL) AS inserted,"
                " count(*) FILTER (WHERE a.id IS NOT NULL AND b.id IS NOT NULL) AS updated "
                + diff.format(t=t)
            )
            assert row is not None
            counts = {k: row[k] for k in ("deleted", "updated", "inserted") if row[k]}
            if counts:
                changes[t] = counts
        breakdowns: dict[str, dict[str, int]] = {}
        for qualified in breakdown_columns:
            t, column = qualified.split(".")
            rows = await conn.fetch(
                f"SELECT b.{column}::text AS value, count(*) AS n "
                + diff.format(t=t).replace("WHERE", "WHERE b.id IS NOT NULL AND")
                + f" GROUP BY b.{column}"
            )
            breakdowns[qualified] = {r["value"]: r["n"] for r in rows}
        return changes, breakdowns
    finally:
        await tr.rollback()


@pytest.mark.parametrize(
    ("scenario", "anchor"), measured_scenarios(), ids=[s.id for s, _ in measured_scenarios()]
)
async def test_expected_impact_matches_the_seed(
    conn: asyncpg.Connection, scenario: SqlScenario, anchor: str
) -> None:
    expected = scenario.expected_impact
    assert expected is not None
    columns = set(expected.breakdowns) | {DERIVED_GROUPS[g][0] for g in expected.derived_groups}
    changes, breakdowns = await measure(conn, pin_now(scenario.sql, anchor), columns)

    assert changes == {
        t: c.model_dump(exclude_defaults=True) for t, c in expected.table_changes.items()
    }
    for qualified, counts in expected.breakdowns.items():
        assert breakdowns[qualified] == counts, qualified
    for group, count in expected.derived_groups.items():
        column, values = DERIVED_GROUPS[group]
        assert sum(n for v, n in breakdowns[column].items() if v in values) == count, group


def test_there_are_measured_scenarios() -> None:
    ids = {s.id for s, _ in measured_scenarios()}
    assert "replit-clean-up-inactive-users" in ids
    assert len(ids) >= 10


def test_pin_now_replaces_only_now() -> None:
    sql = "DELETE FROM t WHERE a < NOW() - interval '1 year' AND b < now()"
    assert pin_now(sql, "2026-10-01") == (
        "DELETE FROM t WHERE a < timestamptz '2026-10-01 00:00:00+00' - interval '1 year'"
        " AND b < timestamptz '2026-10-01 00:00:00+00'"
    )
    with pytest.raises(AssertionError):
        pin_now("SELECT current_timestamp", "2026-10-01")
