"""Load the fake company database (backend plan §8 step 3, BE-07, BE-10, BE-11).

Run from the repo root:
    uv run --project backend python demo_company/seed.py --target local
    uv run --project backend python demo_company/seed.py --target neon [--confirm <host>]

local: rebuilds company_template on the Compose company-db, locks it against connections, and
clones company_main from it. Tests and seeded actions use company_main; the template is never
connected to again (D-019).

neon: replaces the company tables on the Neon main branch that SEED_NEON_OWNER_URL points at.
Without --confirm <host> it only prints the target. Creates blastproof_reader and blastproof_writer
with SQL, so they are not members of neon_superuser (D-077), with the passwords in
COMPANY_DB_READER_URL and COMPANY_DB_WRITER_URL. Claude Code never runs this against
blastproof-company (D-054).
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import asyncpg
from _data import ANCHOR_DATE, generate
from _targets import (
    SeedSettings,
    TargetError,
    describe,
    fail,
    load_settings,
    neon_owner_url,
    password_of,
    quote_ident,
    require_confirmation,
    user_of,
    with_database,
)

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"
TEMPLATE_DB = "company_template"
MAIN_DB = "company_main"
READER = "blastproof_reader"
WRITER = "blastproof_writer"
COMPANY_TABLES = (
    "customer_notes",
    "feature_flags",
    "support_tickets",
    "order_items",
    "orders",
    "products",
    "invoices",
    "payments",
    "subscriptions",
    "users",
    "organizations",
)


async def connect(url: str) -> asyncpg.Connection:
    # TimeZone=UTC like every company-database connection (D-018).
    return await asyncpg.connect(url, timeout=10, server_settings={"TimeZone": "UTC"})


async def load_schema_and_data(conn: asyncpg.Connection) -> dict[str, int]:
    """Create the schema and load the data in one transaction. Returns rows per table."""
    started = time.perf_counter()
    data = generate()
    print(f"Generated data in {time.perf_counter() - started:.1f} s")
    counts: dict[str, int] = {}
    async with conn.transaction():
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        for name, table in data.items():
            await conn.copy_records_to_table(name, records=table.rows, columns=table.columns)
            # Rows were loaded with explicit ids; move each identity sequence past them so agent
            # inserts with a default id work.
            await conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{name}', 'id'),"
                f" (SELECT coalesce(max(id), 0) + 1 FROM {name}), false)"
            )
            counts[name] = len(table.rows)
        await conn.execute(
            "INSERT INTO blastproof.seed_info (anchor_date) VALUES ($1)", ANCHOR_DATE
        )
    await conn.execute("ANALYZE")
    return counts


async def grant_database(conn: asyncpg.Connection, database: str) -> None:
    """Database-level rights, which CREATE DATABASE ... TEMPLATE does not copy."""
    db = quote_ident(database)
    await conn.execute(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC")
    await conn.execute(f"GRANT CONNECT ON DATABASE {db} TO {READER}")
    # TEMPORARY: the shadow run keeps before-hashes in temp tables as the writer (D-018, D-076).
    await conn.execute(f"GRANT CONNECT, TEMPORARY ON DATABASE {db} TO {WRITER}")


async def seed_local(settings: SeedSettings) -> None:
    admin_url = settings.fake_neon_admin_url
    print(f"Target: {describe(admin_url)} (local company-db)")
    admin = await connect(admin_url)
    try:
        missing = {READER, WRITER} - {
            r["rolname"]
            for r in await admin.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY($1)", [READER, WRITER]
            )
        }
        if missing:
            raise TargetError(
                f"roles {sorted(missing)} are missing; the company-db volume predates "
                "docker/company-init. Recreate it: docker compose down -v company-db"
            )
        for db in (MAIN_DB, TEMPLATE_DB):
            await admin.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEMPLATE_DB}")
    finally:
        await admin.close()

    template = await connect(with_database(admin_url, TEMPLATE_DB))
    try:
        counts = await load_schema_and_data(template)
    finally:
        await template.close()

    admin = await connect(admin_url)
    try:
        await admin.execute(f"ALTER DATABASE {TEMPLATE_DB} ALLOW_CONNECTIONS false")
        await admin.execute(f"REVOKE ALL ON DATABASE {TEMPLATE_DB} FROM PUBLIC")
        await admin.execute(f"CREATE DATABASE {MAIN_DB} TEMPLATE {TEMPLATE_DB}")
        await grant_database(admin, MAIN_DB)
    finally:
        await admin.close()
    report(counts, f"{TEMPLATE_DB} (locked) and {MAIN_DB}")


async def ensure_role(conn: asyncpg.Connection, role: str, url: str, variable: str) -> None:
    if not url:
        raise TargetError(f"{variable} is not set; it holds the {role} password.")
    if user_of(url) != role:
        raise TargetError(f"{variable} must connect as {role}.")
    password = password_of(url)
    if not password:
        raise TargetError(f"{variable} has no password.")
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    # A new role is made with least privilege. An existing one only gets its password reset: the
    # Neon owner is not a superuser, and Postgres refuses it any ALTER ROLE that names SUPERUSER,
    # even NOSUPERUSER. Its attributes are checked below instead.
    attributes = (
        "LOGIN PASSWORD %L" if exists else "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L"
    )
    verb = "ALTER" if exists else "CREATE"
    # format() quotes the password as a literal; DDL cannot take bind parameters.
    statement = await conn.fetchval(
        f"SELECT format('%s ROLE %I {attributes}', $1::text, $2::text, $3::text)",
        verb,
        role,
        password,
    )
    await conn.execute(statement)
    privileged = await conn.fetchval(
        "SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname = $1", role
    )
    if privileged:
        raise TargetError(
            f"{role} has SUPERUSER, CREATEDB or CREATEROLE. Delete it in the Neon Console and "
            "re-run, so the seed creates it with SQL and least privilege (D-077)."
        )
    # A role made in the Neon Console is a member of neon_superuser; ALTER ROLE cannot undo that.
    groups = await conn.fetch(
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid"
        " JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = $1",
        role,
    )
    if groups:
        names = ", ".join(sorted(g["rolname"] for g in groups))
        raise TargetError(
            f"{role} is a member of {names}. Delete it in the Neon Console and re-run, so the"
            " seed creates it with SQL and least privilege (D-077)."
        )


async def seed_neon(settings: SeedSettings, confirm: str | None) -> None:
    url = neon_owner_url(settings)
    conn = await connect(url)
    try:
        database = await conn.fetchval("SELECT current_database()")
        existing = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY($1)",
            list(COMPANY_TABLES),
        )
        print(f"Database {database} holds {existing} of the {len(COMPANY_TABLES)} company tables.")
        require_confirmation(url, confirm, f"drop and reload every company table in {database}")
        await ensure_role(conn, READER, settings.company_db_reader_url, "COMPANY_DB_READER_URL")
        await ensure_role(conn, WRITER, settings.company_db_writer_url, "COMPANY_DB_WRITER_URL")
        async with conn.transaction():
            await conn.execute("DROP SCHEMA IF EXISTS blastproof CASCADE")
            for table in COMPANY_TABLES:
                await conn.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
            counts = await load_schema_and_data(conn)
        await grant_database(conn, database)
    finally:
        await conn.close()
    report(counts, database)


def report(counts: dict[str, int], where: str) -> None:
    print(f"Loaded {sum(counts.values()):,} rows into {where}:")
    for name, count in counts.items():
        print(f"  {name:<16} {count:>8,}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=["local", "neon"], required=True)
    parser.add_argument("--confirm", help="neon only: the target's host, to confirm the overwrite")
    args = parser.parse_args(argv)
    settings = load_settings()
    # Scrub only the passwords this run uses: the local dev password ("company") would otherwise
    # blank out ordinary words in a Neon message.
    if args.target == "local":
        urls: tuple[str, ...] = (settings.fake_neon_admin_url,)
    else:
        urls = (
            settings.seed_neon_owner_url,
            settings.company_db_reader_url,
            settings.company_db_writer_url,
        )
    try:
        if args.target == "local":
            asyncio.run(seed_local(settings))
        else:
            asyncio.run(seed_neon(settings, args.confirm))
    except TargetError as exc:
        fail(str(exc), *urls)
    except (
        OSError,
        TimeoutError,
        ValueError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ) as exc:
        # Never print the URL; scrub any password that a driver message might echo.
        fail(f"database error ({type(exc).__name__}): {exc}", *urls)


if __name__ == "__main__":
    sys.exit(main())
