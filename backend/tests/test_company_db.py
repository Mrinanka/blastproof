"""Step 3: the seeded local company database (BE-03 to BE-10, EVAL-03, DW-B03).

Needs `docker compose up -d` and `demo_company/seed.py --target local`. These tests only read
company_main, or write inside transactions they roll back.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from evals.schema import load_scenario_file
from tests.conftest import (
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    SCENARIOS_DIR,
    company_url,
    replit_impact,
    scenario_anchor_date,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

COMPANY_TABLES = {
    "organizations",
    "users",
    "subscriptions",
    "payments",
    "invoices",
    "products",
    "orders",
    "order_items",
    "support_tickets",
    "feature_flags",
    "customer_notes",
}

# BE-05 / D-010: (child table, column) -> (parent table, ON DELETE action).
FOREIGN_KEYS = {
    ("users", "organization_id"): ("organizations", "set null"),
    ("subscriptions", "user_id"): ("users", "cascade"),
    ("payments", "user_id"): ("users", "cascade"),
    ("invoices", "user_id"): ("users", "set null"),
    ("orders", "user_id"): ("users", "cascade"),
    ("order_items", "order_id"): ("orders", "cascade"),
    ("support_tickets", "user_id"): ("users", "cascade"),
    ("customer_notes", "user_id"): ("users", "cascade"),
}
DELETE_ACTIONS = {
    "a": "no action",
    "r": "restrict",
    "c": "cascade",
    "n": "set null",
    "d": "default",
}

# All users by plan: the seed's fixed mix (demo_company/_data.py USER_MIX). Kept here because the
# backend cannot import the seed (D-047); the inactive split comes from incidents.yaml.
PLAN_COUNTS = {"free": 49_388, "pro": 459, "enterprise": 153}


@pytest.fixture
async def admin() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(company_url("company_main"))
    yield conn
    await conn.close()


@pytest.fixture
async def reader() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    yield conn
    await conn.close()


@pytest.fixture
async def writer() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(COMPANY_WRITER_URL)
    yield conn
    await conn.close()


async def assert_denied(conn: asyncpg.Connection, sql: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.execute(sql)


async def run_and_roll_back(conn: asyncpg.Connection, sql: str) -> str:
    tr = conn.transaction()
    await tr.start()
    try:
        return await conn.execute(sql)
    finally:
        await tr.rollback()


# --- Roles and database rights ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["blastproof_reader", "blastproof_writer"])
async def test_company_roles_have_no_special_powers(admin: asyncpg.Connection, role: str) -> None:
    row = await admin.fetchrow(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles"
        " WHERE rolname = $1",
        role,
    )
    assert row is not None, f"{role} is missing"
    assert tuple(row) == (False, False, False, False)
    memberships = await admin.fetchval(
        "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member"
        " WHERE r.rolname = $1",
        role,
    )
    assert memberships == 0


async def test_template_is_locked_and_main_is_open(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT datname, datallowconn FROM pg_database"
        " WHERE datname IN ('company_template', 'company_main')"
    )
    assert {r["datname"]: r["datallowconn"] for r in rows} == {
        "company_template": False,
        "company_main": True,
    }


@pytest.mark.parametrize("database", ["company_template", "company_main"])
async def test_public_has_no_database_rights(admin: asyncpg.Connection, database: str) -> None:
    acl = await admin.fetchval("SELECT datacl::text FROM pg_database WHERE datname = $1", database)
    # NULL would mean the default ACL, which gives PUBLIC CONNECT and TEMPORARY.
    assert acl is not None
    assert not any(entry.startswith("=") for entry in acl.strip("{}").split(","))


@pytest.mark.parametrize(
    ("role", "connect", "temporary"),
    [("blastproof_reader", True, False), ("blastproof_writer", True, True)],
)
async def test_database_rights_per_role(
    admin: asyncpg.Connection, role: str, connect: bool, temporary: bool
) -> None:
    row = await admin.fetchrow(
        "SELECT has_database_privilege($1, 'company_main', 'CONNECT') AS c,"
        " has_database_privilege($1, 'company_main', 'TEMPORARY') AS t",
        role,
    )
    assert row is not None
    assert (row["c"], row["t"]) == (connect, temporary)


# --- Reader: SELECT on the company tables, nothing else -----------------------------------------


async def test_reader_can_read_company_tables(reader: asyncpg.Connection) -> None:
    for table in sorted(COMPANY_TABLES):
        assert await reader.fetchval(f"SELECT count(*) FROM {table}") > 0, table


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customer_notes (user_id, body) VALUES (1, 'x')",
        "UPDATE users SET plan = 'pro' WHERE id = 1",
        "DELETE FROM customer_notes WHERE id = 1",
        "CREATE TABLE public.reader_table (id int)",
        "CREATE TEMPORARY TABLE reader_temp (id int)",
        "SELECT * FROM blastproof.seed_info",
        "SELECT * FROM blastproof.applied_actions",
        "TRUNCATE customer_notes",
    ],
)
async def test_reader_cannot_write_or_see_the_gateway_schema(
    reader: asyncpg.Connection, sql: str
) -> None:
    await assert_denied(reader, sql)


# --- Writer: DML on company tables, append to applied_actions, no DDL ---------------------------


async def test_writer_can_change_company_rows(writer: asyncpg.Connection) -> None:
    tr = writer.transaction()
    await tr.start()
    try:
        # A default id must come after the seeded ids (the seed moves the identity sequences).
        max_id = await writer.fetchval("SELECT max(id) FROM customer_notes")
        new_id = await writer.fetchval(
            "INSERT INTO customer_notes (user_id, body) VALUES (42, 'Called about renewal.')"
            " RETURNING id"
        )
        assert new_id > max_id
        assert await writer.execute("UPDATE users SET plan = 'pro' WHERE id = 42") == "UPDATE 1"
        assert await writer.execute("DELETE FROM customer_notes WHERE id = $1", new_id) == (
            "DELETE 1"
        )
    finally:
        await tr.rollback()


async def test_writer_can_record_and_read_applied_actions(writer: asyncpg.Connection) -> None:
    tr = writer.transaction()
    await tr.start()
    try:
        action_id = await writer.fetchval(
            "INSERT INTO blastproof.applied_actions (action_id) VALUES (gen_random_uuid())"
            " ON CONFLICT DO NOTHING RETURNING action_id"
        )
        assert await writer.fetchval(
            "SELECT count(*) FROM blastproof.applied_actions WHERE action_id = $1", action_id
        )
    finally:
        await tr.rollback()


async def test_writer_can_create_temporary_tables(writer: asyncpg.Connection) -> None:
    # The shadow run's before-hashes live in temp tables (D-018, D-076).
    await run_and_roll_back(writer, "CREATE TEMPORARY TABLE before_users AS SELECT id FROM users")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE blastproof.applied_actions SET committed_at = now()",
        "DELETE FROM blastproof.applied_actions",
        "SELECT * FROM blastproof.seed_info",
        "UPDATE blastproof.seed_info SET anchor_date = '2000-01-01'",
        "CREATE TABLE public.writer_table (id int)",
        "ALTER TABLE users ADD COLUMN x int",
        "DROP TABLE customer_notes",
        "TRUNCATE customer_notes",
        "CREATE ROLE writer_made_role",
    ],
)
async def test_writer_cannot_run_ddl_or_rewrite_the_gateway_schema(
    writer: asyncpg.Connection, sql: str
) -> None:
    await assert_denied(writer, sql)


# --- Schema shape ---------------------------------------------------------------------------------


async def test_exactly_the_eleven_company_tables(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    assert {r["tablename"] for r in rows} == COMPANY_TABLES


async def test_every_company_table_has_a_primary_key(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname IN ('public', 'blastproof') AND c.relkind = 'r'"
        " AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary)"
    )
    assert [r["relname"] for r in rows] == []


async def test_no_triggers(admin: asyncpg.Connection) -> None:
    count = await admin.fetchval(
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE NOT t.tgisinternal AND n.nspname IN ('public', 'blastproof')"
    )
    assert count == 0


async def test_foreign_keys_match_the_plan(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT c.conrelid::regclass::text AS child, a.attname AS column,"
        " c.confrelid::regclass::text AS parent, c.confdeltype::text AS action"
        " FROM pg_constraint c"
        " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]"
        " WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace"
    )
    found = {(r["child"], r["column"]): (r["parent"], DELETE_ACTIONS[r["action"]]) for r in rows}
    assert found == FOREIGN_KEYS


async def test_gateway_schema_tables(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'blastproof' ORDER BY table_name, ordinal_position"
    )
    columns = {(r["table_name"], r["column_name"]) for r in rows}
    assert columns == {
        ("applied_actions", "action_id"),
        ("applied_actions", "committed_at"),
        ("seed_info", "id"),
        ("seed_info", "anchor_date"),
    }


# --- Seed data ------------------------------------------------------------------------------


async def test_anchor_date_matches_the_scenario_files(admin: asyncpg.Connection) -> None:
    # D-052: the seed and the scenario files hold the anchor in two places; this keeps them equal.
    anchor = await admin.fetchval("SELECT anchor_date FROM blastproof.seed_info")
    for name in ("incidents.yaml", "labeled.yaml"):
        assert load_scenario_file(SCENARIOS_DIR / name).anchor_date == anchor, name


async def test_count_by_plan(reader: asyncpg.Connection) -> None:
    # DW-B03's check, automated: the same numbers on every seed.
    rows = await reader.fetch("SELECT plan, count(*) AS n FROM users GROUP BY plan")
    assert {r["plan"]: r["n"] for r in rows} == PLAN_COUNTS


async def test_inactive_users_hold_the_demo_trap(reader: asyncpg.Connection) -> None:
    # The demo's headline split (48,201 free, 9 pro, 3 enterprise), as incidents.yaml states it.
    rows = await reader.fetch(
        "SELECT plan, count(*) AS n FROM users"
        " WHERE last_login_at < $1::date - interval '6 months' GROUP BY plan",
        scenario_anchor_date(),
    )
    assert {r["plan"]: r["n"] for r in rows} == replit_impact().breakdowns["users.plan"]


async def test_no_last_login_is_near_the_six_month_cutoff(
    admin: asyncpg.Connection,
) -> None:
    # The margin keeps the counts exact whatever time of day "now" is, and for "180 days" too.
    near = await admin.fetchval(
        "SELECT count(*) FROM users, blastproof.seed_info s"
        " WHERE last_login_at BETWEEN s.anchor_date - interval '6 months' - interval '13 days'"
        " AND s.anchor_date - interval '6 months' + interval '13 days'"
    )
    assert near == 0


async def test_no_timestamp_is_after_the_anchor(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND data_type IN ('timestamp with time zone', 'date')"
    )
    anchor = await admin.fetchval("SELECT anchor_date FROM blastproof.seed_info")
    for r in rows:
        if r["column_name"] == "due_date":
            continue  # due 14 days after issue, so it may fall after the anchor
        latest = await admin.fetchval(f"SELECT max({r['column_name']}) FROM {r['table_name']}")
        assert latest is None or latest.date() <= anchor, (r["table_name"], r["column_name"])
