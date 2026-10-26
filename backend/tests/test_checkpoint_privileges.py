"""SEC-09, D-174: the checkpointer's tables, their grants, and the role check on its connections.

The app role gets exactly what the checkpointer needs and nothing more: it cannot create, drop,
truncate or alter these tables, and a pool whose URL points at the owner is refused.
"""

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import PoolTimeout
from structlog.testing import capture_logs

from app.db.session import WrongAppRoleError
from app.workflow.checkpointer import _require_app_role, make_checkpointer_pool
from tests.conftest import BLASTPROOF_OWNER_URL, AuthDb, on_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
EXPECTED = {
    "checkpoints": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "checkpoint_blobs": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "checkpoint_writes": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "checkpoint_migrations": {"SELECT"},
}


def owner_url(db: AuthDb) -> str:
    return on_database(BLASTPROOF_OWNER_URL, db.name)


def test_the_app_has_exactly_the_planned_grants_on_every_checkpoint_table(auth_db: AuthDb) -> None:
    with psycopg.connect(owner_url(auth_db)) as conn:
        tables = {
            name
            for (name,) in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename LIKE 'checkpoint%'"
            )
        }
        assert tables == set(EXPECTED)  # a new checkpoint table must be added here on purpose
        actual = {
            table: {
                privilege
                for privilege in PRIVILEGES
                if conn.execute(
                    "SELECT has_table_privilege('blastproof_app', %s, %s)", (table, privilege)
                ).fetchone()[0]  # type: ignore[index]
            }
            for table in tables
        }
    assert actual == EXPECTED


def test_the_owner_owns_them_and_the_app_does_not(auth_db: AuthDb) -> None:
    with psycopg.connect(owner_url(auth_db)) as conn:
        owners = {
            owner
            for (owner,) in conn.execute(
                "SELECT tableowner FROM pg_tables WHERE tablename LIKE 'checkpoint%'"
            )
        }
    assert owners == {"blastproof_owner"}


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE checkpoints",
        "TRUNCATE checkpoints",
        "TRUNCATE checkpoint_blobs",
        "ALTER TABLE checkpoint_writes ADD COLUMN extra int",
        "INSERT INTO checkpoint_migrations (v) VALUES (99)",
        "DELETE FROM checkpoint_migrations",
        "CREATE TABLE checkpoint_future (id int)",
    ],
)
def test_the_app_cannot_change_the_checkpoint_schema(auth_db: AuthDb, statement: str) -> None:
    with (
        psycopg.connect(auth_db.app_url, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(statement)  # type: ignore[arg-type]


def test_the_app_is_refused_setup(auth_db: AuthDb) -> None:
    """The app never creates tables: `setup()` is the migration's job, not the app's (D-173)."""
    with (
        psycopg.connect(auth_db.app_url, autocommit=True, row_factory=dict_row) as conn,  # type: ignore[arg-type]
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        PostgresSaver(conn).setup()  # type: ignore[arg-type]


def test_setup_after_the_migration_is_a_no_op(auth_db: AuthDb) -> None:
    """The migration wrote the version rows, so a later `setup()` does nothing."""
    with psycopg.connect(owner_url(auth_db), autocommit=True, row_factory=dict_row) as conn:  # type: ignore[arg-type]
        before = conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
        PostgresSaver(conn).setup()  # type: ignore[arg-type]
        after = conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
        indexes = {
            row["indexname"]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename LIKE 'checkpoint%'"
            )
        }
    assert [row["v"] for row in before] == list(range(10))
    assert before == after
    assert {
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    } <= indexes


async def test_the_role_check_refuses_the_owner(auth_db: AuthDb) -> None:
    async with await psycopg.AsyncConnection.connect(
        owner_url(auth_db), autocommit=True, row_factory=dict_row
    ) as conn:
        with pytest.raises(WrongAppRoleError, match="must connect as blastproof_app"):
            await _require_app_role(conn)


async def test_the_role_check_accepts_the_app(auth_db: AuthDb) -> None:
    async with await psycopg.AsyncConnection.connect(
        auth_db.app_url, autocommit=True, row_factory=dict_row
    ) as conn:
        await _require_app_role(conn)


async def test_a_pool_on_the_owner_url_never_hands_out_a_connection(auth_db: AuthDb) -> None:
    pool = make_checkpointer_pool(owner_url(auth_db))
    with capture_logs() as logs:
        await pool.open(wait=False)
        try:
            with pytest.raises(PoolTimeout):
                await pool.wait(timeout=2)
        finally:
            await pool.close()
    assert any(entry["event"] == "wrong_app_role" for entry in logs)


async def test_a_pool_on_the_app_url_opens_and_checks_its_connections(auth_db: AuthDb) -> None:
    pool = make_checkpointer_pool(auth_db.app_url)
    await pool.open(wait=True, timeout=10)
    try:
        async with pool.connection() as conn:
            row = await (await conn.execute("SELECT current_user AS u")).fetchone()
            assert row == {"u": "blastproof_app"}
        await pool.check()
    finally:
        await pool.close()
