"""D-173: the migration holds LangGraph's DDL itself. A library upgrade that adds a statement
(or a `CONCURRENTLY` that cannot run inside a migration) fails here, in CI, not in production."""

import importlib.util
from pathlib import Path

from langgraph.checkpoint.postgres.base import BasePostgresSaver

MIGRATION = (
    Path(__file__).resolve().parent.parent / "migrations/versions/0003_langgraph_checkpoints.py"
)


def load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def squash(sql: str) -> str:
    return " ".join(sql.split())


def test_statements_equal_the_librarys_minus_concurrently() -> None:
    library = [squash(m.replace(" CONCURRENTLY", "")) for m in BasePostgresSaver.MIGRATIONS]
    assert [squash(s) for s in load_migration().STATEMENTS] == library


def test_the_library_still_has_concurrent_indexes_only_where_we_expect() -> None:
    # If the library stops using CONCURRENTLY, the removal above is a no-op and still correct; if it
    # adds more, the equality test above catches the new statement. This pins the count we copied.
    concurrent = [m for m in BasePostgresSaver.MIGRATIONS if "CONCURRENTLY" in m]
    assert len(concurrent) == 3


def test_no_statement_in_the_migration_is_concurrent() -> None:
    assert not any("CONCURRENTLY" in s for s in load_migration().STATEMENTS)


def test_the_version_rows_match_the_statement_count() -> None:
    migration = load_migration()
    assert len(migration.STATEMENTS) == len(BasePostgresSaver.MIGRATIONS) == 10
