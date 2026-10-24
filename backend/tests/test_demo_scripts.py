"""BE-08, BE-11: rebase_dates.py and seed.py behave safely.

Every test works on a scratch copy of company_template on the local company-db; the "neon" target
is pointed at that copy, so its guard and load path are tested without ever reaching Neon.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import asyncpg
import pytest

from tests.conftest import (
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    company_url,
    replit_impact,
    run_demo_script,
    scenario_anchor_date,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

ANCHOR = scenario_anchor_date()  # D-052: read from the scenario files
SECRET = "Sup3r-Secret-Value-9a7f"


def query(database: str, sql: str, *args: Any) -> Any:
    async def run() -> Any:
        conn = await asyncpg.connect(company_url(database), server_settings={"TimeZone": "UTC"})
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


@pytest.fixture
def scratch() -> Iterator[str]:
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    query("postgres", f"CREATE DATABASE {name} TEMPLATE company_template")
    try:
        yield name
    finally:
        query("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def anchor_of(database: str) -> date:
    return query(database, "SELECT anchor_date FROM blastproof.seed_info")


def inactive_users(database: str) -> int:
    return query(
        database,
        "SELECT count(*) FROM users, blastproof.seed_info s"
        " WHERE last_login_at < s.anchor_date - interval '6 months'",
    )


# --- rebase_dates.py ------------------------------------------------------------------------------


def test_rebase_moves_every_date_by_whole_days_and_keeps_ages(scratch: str) -> None:
    latest_login = query(scratch, "SELECT max(last_login_at) FROM users")
    earliest_due = query(scratch, "SELECT min(due_date) FROM invoices")
    assert anchor_of(scratch) == ANCHOR

    today = date(2027, 1, 15)
    result = run_demo_script(
        "rebase_dates.py", "--target", "local", "--database", scratch, "--today", today.isoformat()
    )

    assert result.returncode == 0, result.stderr
    shift = timedelta(days=(today - ANCHOR).days)
    assert anchor_of(scratch) == today
    assert query(scratch, "SELECT max(last_login_at) FROM users") == latest_login + shift
    assert query(scratch, "SELECT min(due_date) FROM invoices") == earliest_due + shift
    # Relative ages are unchanged: still the demo's 48,213 inactive users.
    assert inactive_users(scratch) == replit_impact().table_changes["users"].deleted


def test_rebase_twice_on_the_same_day_changes_nothing(scratch: str) -> None:
    args = ("rebase_dates.py", "--target", "local", "--database", scratch, "--today", "2027-01-15")
    assert run_demo_script(*args).returncode == 0
    latest_login = query(scratch, "SELECT max(last_login_at) FROM users")

    second = run_demo_script(*args)

    assert second.returncode == 0, second.stderr
    assert "nothing to do" in second.stdout
    assert query(scratch, "SELECT max(last_login_at) FROM users") == latest_login


def test_rebase_leaves_the_gateway_schema_alone(scratch: str) -> None:
    query(
        scratch,
        "INSERT INTO blastproof.applied_actions (action_id, committed_at)"
        " VALUES (gen_random_uuid(), '2026-09-01T00:00:00Z')",
    )
    run_demo_script(
        "rebase_dates.py", "--target", "local", "--database", scratch, "--today", "2027-01-15"
    )
    committed = query(scratch, "SELECT committed_at FROM blastproof.applied_actions")
    assert committed.date() == date(2026, 9, 1)


def test_rebase_refuses_the_template() -> None:
    result = run_demo_script(
        "rebase_dates.py", "--target", "local", "--database", "company_template"
    )
    assert result.returncode == 1
    assert "locked" in result.stderr


def test_rebase_on_neon_needs_the_exact_host(scratch: str) -> None:
    env = {"SEED_NEON_OWNER_URL": company_url(scratch)}

    missing = run_demo_script("rebase_dates.py", "--target", "neon", env=env)
    wrong = run_demo_script("rebase_dates.py", "--target", "neon", "--confirm", "other", env=env)

    for result in (missing, wrong):
        assert result.returncode == 1
        assert "Target: localhost:5433" in result.stdout
        assert "--confirm localhost" in result.stderr
        assert "Nothing was changed" in result.stderr
    assert anchor_of(scratch) == ANCHOR

    confirmed = run_demo_script(
        "rebase_dates.py", "--target", "neon", "--confirm", "localhost", env=env
    )
    assert confirmed.returncode == 0, confirmed.stderr
    assert anchor_of(scratch) != ANCHOR


# --- seed.py --target neon ------------------------------------------------------------------------


def neon_env(database: str, **overrides: str) -> dict[str, str]:
    env = {
        "SEED_NEON_OWNER_URL": company_url(database),
        "COMPANY_DB_READER_URL": COMPANY_READER_URL,
        "COMPANY_DB_WRITER_URL": COMPANY_WRITER_URL,
    }
    return env | overrides


def test_seed_on_neon_without_confirmation_changes_nothing(scratch: str) -> None:
    query(scratch, "DELETE FROM feature_flags WHERE id = 1")  # a marker a reload would undo

    result = run_demo_script("seed.py", "--target", "neon", env=neon_env(scratch))

    assert result.returncode == 1
    assert "holds 11 of the 11 company tables" in result.stdout
    assert "--confirm localhost" in result.stderr
    assert query(scratch, "SELECT count(*) FROM feature_flags") == 24


def test_seed_on_neon_checks_the_role_urls_before_dropping_anything(scratch: str) -> None:
    env = neon_env(scratch, COMPANY_DB_READER_URL=COMPANY_WRITER_URL)

    result = run_demo_script("seed.py", "--target", "neon", "--confirm", "localhost", env=env)

    assert result.returncode == 1
    assert "must connect as blastproof_reader" in result.stderr
    assert query(scratch, "SELECT count(*) FROM users") == 50_000


def test_seed_on_neon_reloads_with_confirmation(scratch: str) -> None:
    query(scratch, "DELETE FROM feature_flags WHERE id = 1")

    result = run_demo_script(
        "seed.py", "--target", "neon", "--confirm", "localhost", env=neon_env(scratch)
    )

    assert result.returncode == 0, result.stderr
    assert query(scratch, "SELECT count(*) FROM feature_flags") == 25
    assert anchor_of(scratch) == ANCHOR
    acl = query(scratch, "SELECT datacl::text FROM pg_database WHERE datname = current_database()")
    assert not any(entry.startswith("=") for entry in acl.strip("{}").split(","))
    assert "blastproof_writer=Tc/" in acl  # CONNECT and TEMPORARY (D-076)


def test_seed_refuses_a_pooled_neon_endpoint() -> None:
    url = f"postgresql://owner:{SECRET}@ep-cool-name-123-pooler.us-east-2.aws.neon.tech/neondb"

    result = run_demo_script("seed.py", "--target", "neon", env={"SEED_NEON_OWNER_URL": url})

    assert result.returncode == 1
    assert "direct endpoint" in result.stderr
    assert SECRET not in result.stdout + result.stderr


def test_only_the_targets_passwords_are_scrubbed() -> None:
    # The local admin password here is a word in the Neon message. Scrubbing every configured
    # password used to turn "every company table" into "every *** table".
    neon = f"postgresql://owner:{SECRET}@ep-cool-name-123-pooler.us-east-2.aws.neon.tech/neondb"
    local = "postgresql://company:endpoint@localhost:5433/postgres"

    result = run_demo_script(
        "seed.py",
        "--target",
        "neon",
        env={"SEED_NEON_OWNER_URL": neon, "FAKE_NEON_ADMIN_URL": local},
    )

    assert result.returncode == 1
    assert "must use the direct endpoint" in result.stderr
    assert SECRET not in result.stdout + result.stderr


def test_seed_on_neon_needs_the_owner_url() -> None:
    result = run_demo_script("seed.py", "--target", "neon", env={"SEED_NEON_OWNER_URL": ""})
    assert result.returncode == 1
    assert "SEED_NEON_OWNER_URL is not set" in result.stderr


# --- Errors never show a password -----------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("seed.py", "--target", "local"),
        ("rebase_dates.py", "--target", "local"),
    ],
    ids=["seed", "rebase"],
)
def test_connection_errors_hide_the_password(args: tuple[str, ...]) -> None:
    # Port 1 on loopback: nothing listens there, so the connection is refused at once.
    url = f"postgresql://company:{SECRET}@127.0.0.1:1/postgres"

    result = run_demo_script(*args, env={"FAKE_NEON_ADMIN_URL": url})

    assert result.returncode == 1
    assert "database error" in result.stderr
    assert "Traceback" not in result.stderr
    assert SECRET not in result.stdout + result.stderr
