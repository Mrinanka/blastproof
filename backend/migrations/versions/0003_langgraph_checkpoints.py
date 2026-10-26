"""LangGraph's checkpoint tables (step 14, D-173, D-174).

The library's own `setup()` is not used: it runs `CREATE INDEX CONCURRENTLY` on its own connection,
which is not atomic with the rest of a migration. This revision holds the same 10 statements with
`CONCURRENTLY` removed, so a failure leaves nothing half-built, and writes the
`checkpoint_migrations` rows that make a later `setup()` a no-op.
`tests/test_checkpoint_migration.py` fails when a library upgrade adds a statement.

The app gets exactly what the checkpointer needs: SELECT, INSERT, UPDATE, DELETE on the three data
tables and SELECT on `checkpoint_migrations`. No `ALTER DEFAULT PRIVILEGES` (it would also hand out
UPDATE and DELETE on later tables, `audit_events` among them).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "blastproof_app"

# BasePostgresSaver.MIGRATIONS of langgraph-checkpoint-postgres 3.2.0, minus CONCURRENTLY.
STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);""",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    "SELECT 1;",
    "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);",
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';",
)

TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
    op.execute(
        "INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, "
        f"{len(STATEMENTS) - 1}) ON CONFLICT DO NOTHING"
    )
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON checkpoint_migrations TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
