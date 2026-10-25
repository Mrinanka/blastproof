"""What the company database looks like, read from `pg_catalog` (backend plan step 10).

`ROW_HASH_SQL` is the only definition of a row's hash: the shadow run and the commit's drift check
(step 16) both use it, so a hash taken on a shadow copy equals the hash on the real database.
Only the `public` schema is described; the `blastproof` schema is not hashed (agent SQL that
touches it is blocked by the rules).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import asyncpg

# `t` is the alias of the table being hashed. The text of `row_to_json` depends on session settings
# (TimeZone, DateStyle, IntervalStyle, extra_float_digits, bytea_output); every connection that
# hashes rows pins them (`company_db.HASH_SESSION_SETTINGS`).
ROW_HASH_SQL = "md5(row_to_json(t)::text)"

# pg_constraint.confdeltype
DELETE_ACTIONS = {
    "a": "no_action",
    "r": "restrict",
    "c": "cascade",
    "n": "set_null",
    "d": "set_default",
}


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pk_json_sql(alias: str, pk_columns: Sequence[str]) -> str:
    """A table's primary key as a jsonb object, `{"id": 7}`; composite keys have one entry each."""
    pairs = ", ".join(
        f"{quote_literal(c)}, {quote_ident(alias)}.{quote_ident(c)}" for c in pk_columns
    )
    return f"jsonb_build_object({pairs})"


@dataclass(frozen=True)
class CompanyTable:
    name: str
    columns: tuple[str, ...]
    pk_columns: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKey:
    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    on_delete: str  # a value of DELETE_ACTIONS


@dataclass(frozen=True)
class CompanySchema:
    tables: dict[str, CompanyTable]
    foreign_keys: tuple[ForeignKey, ...] = field(default_factory=tuple)

    def columns_by_table(self) -> dict[str, set[str]]:
        return {name: set(t.columns) for name, t in self.tables.items()}


_COLUMNS_SQL = """
SELECT c.relname::text AS table_name, a.attname::text AS column_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname, a.attnum
"""

_PRIMARY_KEYS_SQL = """
SELECT cl.relname::text AS table_name,
       array(SELECT a.attname::text
             FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS columns
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
WHERE con.contype = 'p' AND n.nspname = 'public'
"""

_FOREIGN_KEYS_SQL = """
SELECT cl.relname::text AS child, pl.relname::text AS parent, con.confdeltype::text AS on_delete,
       array(SELECT a.attname::text
             FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS child_columns,
       array(SELECT a.attname::text
             FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
             ORDER BY k.ord) AS parent_columns
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
JOIN pg_class pl ON pl.oid = con.confrelid
JOIN pg_namespace pn ON pn.oid = pl.relnamespace
WHERE con.contype = 'f' AND n.nspname = 'public' AND pn.nspname = 'public'
ORDER BY cl.relname, con.conname
"""


async def read_schema(conn: asyncpg.Connection) -> CompanySchema:
    """Every `public` table with its columns and primary key, and the foreign keys between them.

    A table without a primary key has an empty `pk_columns`; the caller refuses it.
    """
    columns: dict[str, list[str]] = {}
    for row in await conn.fetch(_COLUMNS_SQL):
        columns.setdefault(row["table_name"], []).append(row["column_name"])
    keys = {row["table_name"]: tuple(row["columns"]) for row in await conn.fetch(_PRIMARY_KEYS_SQL)}
    foreign_keys = tuple(
        ForeignKey(
            child=row["child"],
            child_columns=tuple(row["child_columns"]),
            parent=row["parent"],
            parent_columns=tuple(row["parent_columns"]),
            on_delete=DELETE_ACTIONS[row["on_delete"]],
        )
        for row in await conn.fetch(_FOREIGN_KEYS_SQL)
    )
    tables = {
        name: CompanyTable(name=name, columns=tuple(cols), pk_columns=keys.get(name, ()))
        for name, cols in columns.items()
    }
    return CompanySchema(tables=tables, foreign_keys=foreign_keys)
