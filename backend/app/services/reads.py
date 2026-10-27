"""Reads on the company database, as the read-only `blastproof_reader` role (step 6).

The classifier (step 8) decides what is a read; only a statement it calls `read` gets here (D-124).
Behind it, the reader role (SELECT only), a READ ONLY transaction and the 5 s statement timeout each
stop writes on their own.

`run_read` fetches one row more than the cap, so it can tell the agent the result was cut short.
"""

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

MAX_ROWS = 500


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
