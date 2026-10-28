"""deploy/neon/blastproof-app.sql sets Neon's blastproof-app up like the dev database (SEC-19).

The fast tests read the script. The integration test runs it on the Compose company-db, as a
simulated non-superuser `neondb_owner`, in a scratch database, and checks what the dev database's
init script (docker/blastproof-init/01-roles.sql) gives: the same owners and access lists.
"""

import re
import uuid
from urllib.parse import urlsplit

import psycopg
import pytest

from tests.conftest import COMPANY_ADMIN_URL
from tests.test_dockerfile import REPO_ROOT

SCRIPT = (REPO_ROOT / "deploy" / "neon" / "blastproof-app.sql").read_text(encoding="utf-8")
INIT = (REPO_ROOT / "docker" / "blastproof-init" / "01-roles.sql").read_text(encoding="utf-8")
ROLE_NAMES = ("blastproof_owner", "blastproof_app")


def statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def access_statements(sql: str, database: str) -> list[str]:
    """The REVOKE/GRANT/ALTER SCHEMA lines, with the database name replaced by a marker."""
    wanted = ("REVOKE", "GRANT CONNECT", "GRANT USAGE", "ALTER SCHEMA")
    return [re.sub(rf"\b{database}\b", "<DB>", s) for s in statements(sql) if s.startswith(wanted)]


def test_the_script_has_the_two_placeholders_and_no_literal_password() -> None:
    assert SCRIPT.count("<OWNER_PASSWORD>") == 1
    assert SCRIPT.count("<APP_PASSWORD>") == 1
    for statement in statements(SCRIPT):
        if statement.startswith("CREATE ROLE"):
            assert re.search(r"PASSWORD '<(OWNER|APP)_PASSWORD>'$", statement), statement


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_both_roles_are_plain_login_roles(role: str) -> None:
    (create,) = [s for s in statements(SCRIPT) if s.startswith(f"CREATE ROLE {role} ")]
    assert "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in create


def test_the_access_lines_equal_the_dev_init_script() -> None:
    assert access_statements(SCRIPT, "neondb") == access_statements(INIT, "blastproof")
    assert len(access_statements(SCRIPT, "neondb")) == 5


def test_the_owner_is_made_database_owner_after_the_grant() -> None:
    sql = statements(SCRIPT)
    grant = sql.index("GRANT blastproof_owner TO neondb_owner")
    assert sql.index("ALTER DATABASE neondb OWNER TO blastproof_owner") > grant


def test_pgvector_is_created() -> None:
    assert "CREATE EXTENSION IF NOT EXISTS vector" in statements(SCRIPT)


# --- integration: run it for real ----------------------------------------------------------


def login_url(user: str, password: str, database: str) -> str:
    """The company-db server's URL, as another login, on another database."""
    parts = urlsplit(COMPANY_ADMIN_URL)
    return f"postgresql://{user}:{password}@{parts.hostname}:{parts.port}/{database}"


def acl_holders(conn: psycopg.Connection[tuple[object, ...]], query: str) -> set[str]:
    """Who appears in an access list; an empty name would be PUBLIC."""
    row = conn.execute(query).fetchone()
    assert row is not None and isinstance(row[0], list)
    return {str(entry).split("=")[0] for entry in row[0]}


@pytest.mark.integration
def test_the_script_sets_up_a_database_like_the_dev_one() -> None:
    database = f"neon_scratch_{uuid.uuid4().hex[:10]}"
    owner_pw, app_pw = "owner-pw-" + uuid.uuid4().hex, "app-pw-" + uuid.uuid4().hex
    # Neon's neondb_owner can create roles and databases but is not a superuser; the script names
    # it, so a role of that exact name is made for the run and dropped after.
    simulated_pw = "sim-pw-" + uuid.uuid4().hex
    with psycopg.connect(COMPANY_ADMIN_URL, autocommit=True) as admin:
        taken = {r[0] for r in admin.execute("SELECT rolname FROM pg_roles").fetchall()}
        assert not taken & {"neondb_owner", *ROLE_NAMES}, "this server already has these roles"
        admin.execute(
            f"CREATE ROLE neondb_owner LOGIN CREATEROLE CREATEDB PASSWORD '{simulated_pw}'"
        )
        admin.execute(f"CREATE DATABASE {database} OWNER neondb_owner")
    try:
        with psycopg.connect(
            login_url("neondb_owner", simulated_pw, database), autocommit=True
        ) as conn:
            for statement in statements(SCRIPT):
                if statement.startswith("CREATE EXTENSION"):
                    continue  # plain postgres has no pgvector
                statement = statement.replace("<OWNER_PASSWORD>", owner_pw)
                statement = statement.replace("<APP_PASSWORD>", app_pw)
                statement = re.sub(r"neondb(?!_owner)", database, statement)
                conn.execute(statement)  # type: ignore[arg-type]
            owners = conn.execute(
                "SELECT pg_get_userbyid(d.datdba), pg_get_userbyid(n.nspowner)"
                " FROM pg_database d, pg_namespace n"
                " WHERE d.datname = current_database() AND n.nspname = 'public'"
            ).fetchone()
            on_database = acl_holders(
                conn,
                "SELECT array_agg(a::text) FROM pg_database d, unnest(d.datacl) a"
                " WHERE d.datname = current_database()",
            )
            on_schema = acl_holders(
                conn,
                "SELECT array_agg(a::text) FROM pg_namespace n, unnest(n.nspacl) a"
                " WHERE n.nspname = 'public'",
            )
        assert owners == ("blastproof_owner", "blastproof_owner")
        assert "" not in on_database and "" not in on_schema  # nothing is granted to PUBLIC
        assert {"blastproof_app", "blastproof_owner"} <= on_database
        assert {"blastproof_app", "blastproof_owner"} <= on_schema

        # The app can log in and read the catalog, but create nothing, not even a temp table.
        with psycopg.connect(login_url("blastproof_app", app_pw, database), autocommit=True) as app:
            assert app.execute("SELECT 1").fetchone() == (1,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute("CREATE TABLE t (id int)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute("CREATE TEMP TABLE t (id int)")
        # The owner can create tables: it is the migrator.
        with psycopg.connect(
            login_url("blastproof_owner", owner_pw, database), autocommit=True
        ) as o:
            o.execute("CREATE TABLE t (id int)")
    finally:
        with psycopg.connect(COMPANY_ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
            for role in (*ROLE_NAMES, "neondb_owner"):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
