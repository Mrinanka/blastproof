"""Reads on the company database, as the read-only `blastproof_reader` role (step 6).

`check_read` is a temporary first layer until the step 8 classifier replaces it (D-103): anything
that is not clearly one plain SELECT is refused before it reaches the database. Behind it, the
reader role (SELECT only), a READ ONLY transaction and the 5 s statement timeout each stop writes
on their own.

`run_read` fetches one row more than the cap, so it can tell the agent the result was cut short.
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_ROWS = 500
MAX_SQL_BYTES = 20 * 1024

# Functions with a side effect even inside a SELECT (ending sessions, taking locks, changing
# settings, moving sequences, sending notifications, reaching other servers or large objects).
_SIDE_EFFECT_FUNCTIONS = frozenset(
    {"pg_terminate_backend", "pg_cancel_backend", "set_config", "nextval", "setval", "pg_notify"}
)
_SIDE_EFFECT_PREFIXES = ("pg_advisory_", "pg_try_advisory_", "dblink", "lo_")

# Anywhere in the tree, these mean "not a plain read": data-modifying CTEs, SELECT INTO,
# FOR UPDATE/SHARE, and statements sqlglot could only parse as an opaque command.
_REFUSED_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Into,
    exp.Lock,
    exp.Command,
    exp.Transaction,
    exp.Set,
    exp.Copy,
)


@dataclass(frozen=True)
class ReadCheck:
    ok: bool
    # Why it was refused, as a short code for the logs (never the SQL itself).
    reason: str | None = None
    tables: list[str] = field(default_factory=list)


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def _is_side_effect_function(name: str) -> bool:
    return name in _SIDE_EFFECT_FUNCTIONS or name.startswith(_SIDE_EFFECT_PREFIXES)


def check_read(sql: str) -> ReadCheck:
    """Accept one plain SELECT (WITH, UNION, comments and a trailing `;` are fine)."""
    if len(sql.encode()) > MAX_SQL_BYTES:
        return ReadCheck(ok=False, reason="too_long")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError:
        return ReadCheck(ok=False, reason="unparseable")
    if not statements:
        return ReadCheck(ok=False, reason="empty")
    if len(statements) > 1:
        return ReadCheck(ok=False, reason="multiple_statements")
    [statement] = statements
    if not isinstance(statement, exp.Select | exp.SetOperation):
        return ReadCheck(ok=False, reason="not_a_select")
    if any(statement.find_all(*_REFUSED_NODES)):
        return ReadCheck(ok=False, reason="not_a_plain_read")
    if any(_is_side_effect_function(_function_name(f)) for f in statement.find_all(exp.Func)):
        return ReadCheck(ok=False, reason="side_effect_function")
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    tables = sorted(
        {
            f"{table.db}.{table.name}" if table.db else table.name
            for table in statement.find_all(exp.Table)
            if table.name and table.name not in cte_names
        }
    )
    return ReadCheck(ok=True, tables=tables)


@dataclass(frozen=True)
class ReadResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def json_safe(value: Any) -> Any:
    """A value the MCP reply can carry as JSON: dates as ISO 8601, numbers kept exact as text."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal | uuid.UUID | timedelta):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, asyncpg.Record):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


async def run_read(conn: asyncpg.Connection, sql: str) -> ReadResult:
    """Run one statement in a READ ONLY transaction and return at most MAX_ROWS rows.

    Postgres errors (including the statement timeout) propagate to the caller.
    """
    async with conn.transaction(readonly=True):
        statement = await conn.prepare(sql)
        columns = [attribute.name for attribute in statement.get_attributes()]
        cursor = await statement.cursor()
        records = await cursor.fetch(MAX_ROWS + 1)
    rows = [[json_safe(value) for value in record.values()] for record in records[:MAX_ROWS]]
    return ReadResult(columns=columns, rows=rows, truncated=len(records) > MAX_ROWS)


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str | None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    description: str | None


# Tables and views in `public` the reader may select from. The `blastproof` schema is never listed.
_LIST_TABLES = """
SELECT c.relname, obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname
"""

_FIND_TABLE = """
SELECT c.oid, obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = $1 AND c.relkind IN ('r', 'p', 'v', 'm')
  AND has_table_privilege(c.oid, 'SELECT')
"""

_COLUMNS = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
       col_description(a.attrelid, a.attnum)
FROM pg_attribute a
WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""


async def list_tables(conn: asyncpg.Connection) -> list[TableInfo]:
    async with conn.transaction(readonly=True):
        records = await conn.fetch(_LIST_TABLES)
    return [TableInfo(name=r[0], description=r[1]) for r in records]


async def describe_table(
    conn: asyncpg.Connection, name: str
) -> tuple[TableInfo, list[ColumnInfo]] | None:
    """Columns and types of one table in `public`, or None if there is no such readable table."""
    bare = name.removeprefix("public.")
    async with conn.transaction(readonly=True):
        found = await conn.fetchrow(_FIND_TABLE, bare)
        if found is None:
            return None
        records = await conn.fetch(_COLUMNS, found[0])
    columns = [ColumnInfo(name=r[0], type=r[1], nullable=r[2], description=r[3]) for r in records]
    return TableInfo(name=bare, description=found[1]), columns
