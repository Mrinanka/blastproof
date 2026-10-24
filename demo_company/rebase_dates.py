"""Shift every date and timestamp in the company tables to today (backend plan §8 step 3, BE-08).

The seed's dates are relative to its anchor (D-017). This moves every timestamp and date column in
the public schema by the same whole number of days (today minus the anchor, UTC) and records the
new anchor in blastproof.seed_info. Relative ages are unchanged, so "inactive for 6 months" still
counts exactly the same users on any day. Running it again on the same day changes nothing.

Owner-run on Neon only (D-029): it rewrites every row of the main branch, which makes any action
awaiting review drift, so run it with an empty review queue. Claude Code never runs it on Neon.

    uv run --project backend python demo_company/rebase_dates.py --target neon [--confirm <host>]
    uv run --project backend python demo_company/rebase_dates.py --target local --database <name>
"""

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime

import asyncpg
from _targets import (
    SeedSettings,
    TargetError,
    describe,
    fail,
    load_settings,
    neon_owner_url,
    quote_ident,
    require_confirmation,
    with_database,
)

DATE_TYPES = ("date", "timestamp with time zone", "timestamp without time zone")


async def rebase(conn: asyncpg.Connection, today: date) -> None:
    anchor = await conn.fetchval("SELECT anchor_date FROM blastproof.seed_info")
    if anchor is None:
        raise TargetError("blastproof.seed_info has no anchor date; is this a seeded database?")
    days = (today - anchor).days
    if days == 0:
        print(f"Already anchored at {today}; nothing to do.")
        return
    columns = await conn.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'public' AND data_type = ANY($1)"
        " ORDER BY table_name, ordinal_position",
        list(DATE_TYPES),
    )
    by_table: dict[str, list[str]] = {}
    for row in columns:
        column = quote_ident(row["column_name"])
        # date + integer stays a date; timestamps move by an interval of whole days.
        shift = "$1::integer" if row["data_type"] == "date" else "make_interval(days => $1)"
        by_table.setdefault(row["table_name"], []).append(f"{column} = {column} + {shift}")
    async with conn.transaction():
        for table, assignments in by_table.items():
            await conn.execute(
                f"UPDATE public.{quote_ident(table)} SET {', '.join(assignments)}", days
            )
        await conn.execute("UPDATE blastproof.seed_info SET anchor_date = $1", today)
    print(f"Moved {len(columns)} columns in {len(by_table)} tables by {days} days to {today}.")


async def run(settings: SeedSettings, args: argparse.Namespace) -> None:
    today = args.today or datetime.now(UTC).date()
    if args.target == "local":
        if args.database == "company_template":
            raise TargetError("company_template is locked and never changed; seed again instead.")
        url = with_database(settings.fake_neon_admin_url, args.database)
        print(f"Target: {describe(url)} (local company-db)")
    else:
        url = neon_owner_url(settings)
        require_confirmation(url, args.confirm, "shift every date in the company tables")
    conn = await asyncpg.connect(url, timeout=10, server_settings={"TimeZone": "UTC"})
    try:
        await rebase(conn, today)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=["local", "neon"], required=True)
    parser.add_argument("--database", default="company_main", help="local only")
    parser.add_argument("--confirm", help="neon only: the target's host, to confirm the rewrite")
    parser.add_argument("--today", type=date.fromisoformat, help="default: today (UTC)")
    args = parser.parse_args(argv)
    settings = load_settings()
    # Scrub only the target's password (see seed.py).
    target_url = (
        settings.fake_neon_admin_url if args.target == "local" else settings.seed_neon_owner_url
    )
    urls = (target_url,)
    try:
        asyncio.run(run(settings, args))
    except TargetError as exc:
        fail(str(exc), *urls)
    except (
        OSError,
        TimeoutError,
        ValueError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ) as exc:
        fail(f"database error ({type(exc).__name__}): {exc}", *urls)


if __name__ == "__main__":
    sys.exit(main())
