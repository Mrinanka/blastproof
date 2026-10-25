"""The shadow executor (backend plan step 10): measure what a write would do, don't predict it.

It copies the company database (a Neon branch, or a local clone), runs `pinned_sql` inside a
transaction that is rolled back, and diffs every table by primary key using one Postgres-computed
hash per row. The copy is always deleted, concurrency is limited, and every error fails closed:
`ShadowFailedError` carries a reason code and nothing else (no SQL, no row values).
"""

import asyncio
import contextlib
import json
import secrets
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from app.services import company_db
from app.services.classifier import Classification, Kind
from app.services.company_schema import (
    ROW_HASH_SQL,
    CompanySchema,
    ForeignKey,
    pk_json_sql,
    quote_ident,
    read_schema,
)
from app.services.masking import MASKED_PLACEHOLDER, mask_text
from app.services.neon import NeonBusyError, NeonClient, NeonError, ShadowTarget
from app.services.policy import DiffFacts, Policy, PolicyFileError, TableFacts, check_columns

SAMPLES_PER_SIDE = 50
MAX_CASCADE_DEPTH = 3
PREVIEW_CHANGES = ("deleted", "updated", "inserted")

logger = structlog.get_logger()


class ShadowBusyError(Exception):
    """No shadow slot (or no Neon branch) became free in time. The caller may try again later."""

    def __init__(self, reason_code: str = "shadow_busy") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ShadowFailedError(Exception):
    """The preview could not be measured. The write must be blocked (fail closed)."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# --- Results ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TableDiff:
    table: str
    deleted: int = 0
    updated: int = 0
    inserted: int = 0
    rows_before: int = 0
    is_cascade: bool = False

    @property
    def changed(self) -> int:
        return self.deleted + self.updated + self.inserted


@dataclass(frozen=True)
class RowChange:
    """One changed row. Goes to `preview_rows`; never into `impact`."""

    table: str
    primary_key: dict[str, Any]
    before_hash: str | None  # None for an inserted row
    change: str  # deleted | updated | inserted


@dataclass(frozen=True)
class CascadePath:
    """What the foreign keys say should follow from deleting rows of `target`."""

    target: str
    edges: tuple[ForeignKey, ...]  # child-to-parent, nearest the target first
    on_delete: str  # of the last edge: cascade | set_null | set_default
    count: int

    @property
    def table(self) -> str:
        return self.edges[-1].child

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "table": self.table,
            "via": [edge.child for edge in self.edges[:-1]],
            "on_delete": self.on_delete,
            "count": self.count,
        }


@dataclass(frozen=True)
class SampleRow:
    table: str
    change: str
    side: str  # before | after
    values: dict[str, Any]  # masked


@dataclass(frozen=True)
class Diff:
    tables: dict[str, TableDiff]  # every table, changed or not
    breakdowns: dict[str, dict[str, int]] = field(default_factory=dict)
    derived_groups: dict[str, int] = field(default_factory=dict)
    cascade_expectations: tuple[CascadePath, ...] = ()
    samples: tuple[SampleRow, ...] = ()
    changes: tuple[RowChange, ...] = ()

    def facts(self) -> DiffFacts:
        return DiffFacts(
            tables={
                name: TableFacts(
                    deleted=t.deleted,
                    updated=t.updated,
                    inserted=t.inserted,
                    rows_before=t.rows_before,
                    is_cascade=t.is_cascade,
                )
                for name, t in self.tables.items()
            },
            derived_groups=dict(self.derived_groups),
        )

    def impact_json(self) -> dict[str, Any]:
        """For `actions.impact`: counts, groups and masked samples; no per-row keys or hashes."""
        return {
            "table_changes": {
                name: {"deleted": t.deleted, "updated": t.updated, "inserted": t.inserted}
                for name, t in self.tables.items()
                if t.changed
            },
            "rows_before": {name: t.rows_before for name, t in self.tables.items()},
            "cascade_tables": sorted(name for name, t in self.tables.items() if t.is_cascade),
            "breakdowns": self.breakdowns,
            "derived_groups": self.derived_groups,
            "samples": [
                {"table": s.table, "change": s.change, "side": s.side, "values": s.values}
                for s in self.samples
            ],
        }

    def cascade_json(self) -> list[dict[str, Any]]:
        return [path.to_json() for path in self.cascade_expectations]


@dataclass(frozen=True)
class ShadowResult:
    diff: Diff
    preview_ms: int


# --- Pure helpers -----------------------------------------------------------------------------


def fk_paths(
    foreign_keys: Iterable[ForeignKey], target: str, *, max_depth: int = MAX_CASCADE_DEPTH
) -> list[tuple[ForeignKey, ...]]:
    """Chains of foreign keys through which deleting a row of `target` reaches other tables.

    A chain continues only through ON DELETE CASCADE (a SET NULL row survives, so nothing beneath
    it follows). NO ACTION and RESTRICT keys are skipped: they would stop the delete, and the shadow
    run already shows that as a statement error. A table is never visited twice on one chain.
    """
    by_parent: dict[str, list[ForeignKey]] = defaultdict(list)
    for fk in foreign_keys:
        by_parent[fk.parent].append(fk)
    paths: list[tuple[ForeignKey, ...]] = []

    def walk(table: str, chain: tuple[ForeignKey, ...], seen: frozenset[str]) -> None:
        for fk in sorted(by_parent.get(table, []), key=lambda f: (f.child, f.child_columns)):
            if fk.on_delete not in ("cascade", "set_null", "set_default") or fk.child in seen:
                continue
            path = (*chain, fk)
            paths.append(path)
            if fk.on_delete == "cascade" and len(path) < max_depth:
                walk(fk.child, path, seen | {fk.child})

    walk(target, (), frozenset({target}))
    return paths


def mask_row(policy: Policy, table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """A row with sensitive columns replaced and emails and phone numbers hidden inside text."""
    masked: dict[str, Any] = {}
    for column, value in row.items():
        if value is None:
            masked[column] = None
        elif policy.is_sensitive(table, column):
            masked[column] = MASKED_PLACEHOLDER
        elif isinstance(value, str):
            masked[column] = mask_text(value)
        else:
            masked[column] = value
    return masked


def _join_on(
    left: str, left_columns: Sequence[str], right: str, right_columns: Sequence[str]
) -> str:
    return " AND ".join(
        f"{left}.{quote_ident(lc)} = {right}.{quote_ident(rc)}"
        for lc, rc in zip(left_columns, right_columns, strict=True)
    )


def _cascade_count_sql(
    path: Sequence[ForeignKey], target_pk: Sequence[str], diff: str, before_rel: Any
) -> str:
    """Count the rows at the far end of `path` that hang off the deleted keys in `diff`.

    Every table is read as it was before the write (`before_rel`), because the cascade has
    already removed the children inside the transaction.
    """
    last = len(path)
    joins = []
    for i in range(last, 0, -1):
        fk = path[i - 1]
        joins.append(
            f"JOIN {before_rel(fk.parent)} a{i - 1} ON "
            + _join_on(f"a{i}", fk.child_columns, f"a{i - 1}", fk.parent_columns)
        )
    return (
        f"SELECT count(*) FROM {before_rel(path[-1].child)} a{last} "
        + " ".join(joins)
        + f" WHERE {pk_json_sql('a0', target_pk)} IN"
        f" (SELECT pk FROM {diff} WHERE change = 'deleted')"
    )


# --- The runner -------------------------------------------------------------------------------


class ShadowRunner:
    def __init__(
        self,
        *,
        policy: Policy,
        clients: Mapping[str, NeonClient],
        max_concurrent: int = 2,
        slot_wait_seconds: float = 10.0,
        timeout_seconds: float = 30.0,
        delete_timeout_seconds: float = 10.0,
    ) -> None:
        self._policy = policy
        self._clients = clients
        self._slots = asyncio.Semaphore(max_concurrent)
        self._slot_wait = slot_wait_seconds
        self._timeout = timeout_seconds
        self._delete_timeout = delete_timeout_seconds

    async def run(
        self, *, action_id: UUID, profile: str, classification: Classification
    ) -> ShadowResult:
        """Measure `classification.pinned_sql` on a fresh copy. Raises ShadowBusy/FailedError."""
        if classification.kind != Kind.WRITE or not classification.pinned_sql:
            raise ShadowFailedError("not_a_write")
        client = self._clients.get(profile)
        if client is None:
            raise ShadowFailedError("neon_not_configured")
        try:
            async with asyncio.timeout(self._slot_wait):
                await self._slots.acquire()
        except TimeoutError:
            raise ShadowBusyError() from None
        try:
            with structlog.contextvars.bound_contextvars(action_id=str(action_id)):
                return await self._run_on_copy(client, action_id, classification)
        finally:
            self._slots.release()

    async def _run_on_copy(
        self, client: NeonClient, action_id: UUID, classification: Classification
    ) -> ShadowResult:
        target: ShadowTarget | None = None
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout):
                target = await client.create_shadow(action_id)
                async with company_db.connect_target(target) as conn:
                    diff = await self._measure(conn, classification)
            preview_ms = round((time.perf_counter() - started) * 1000)
        except TimeoutError:
            logger.error("shadow_failed", reason_code="shadow_timeout")
            raise ShadowFailedError("shadow_timeout") from None
        except NeonBusyError as exc:
            logger.warning("shadow_busy", reason_code=exc.reason_code)
            raise ShadowBusyError(exc.reason_code) from None
        except (ShadowFailedError, ShadowBusyError):
            raise
        except NeonError as exc:
            logger.error("shadow_failed", reason_code=exc.reason_code)
            raise ShadowFailedError(exc.reason_code) from None
        except company_db.CompanyDbUnavailableError:
            logger.error("shadow_failed", reason_code="shadow_connect_failed")
            raise ShadowFailedError("shadow_connect_failed") from None
        except PolicyFileError:
            logger.error("shadow_failed", reason_code="policy_columns")
            raise ShadowFailedError("policy_columns") from None
        except Exception as exc:
            # The class only: a Postgres message can hold row values.
            logger.error("shadow_failed", reason_code="shadow_error", error=type(exc).__name__)
            raise ShadowFailedError("shadow_error") from None
        finally:
            if target is not None:
                await self._delete(client, target)
        logger.info(
            "shadow_done",
            preview_ms=preview_ms,
            changed_tables=sum(1 for t in diff.tables.values() if t.changed),
            changed_rows=len(diff.changes),
        )
        return ShadowResult(diff=diff, preview_ms=preview_ms)

    async def _delete(self, client: NeonClient, target: ShadowTarget) -> None:
        """Always remove the copy. A failed delete is logged, never raised: the first error wins."""
        try:
            async with asyncio.timeout(self._delete_timeout):
                await asyncio.shield(client.delete_shadow(target))
        except Exception as exc:
            logger.error(
                "shadow_delete_failed",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
            )

    # --- On the copy --------------------------------------------------------------------------

    def _check(self, schema: CompanySchema, classification: Classification) -> None:
        for table in schema.tables.values():
            if not table.pk_columns:
                raise ShadowFailedError("table_without_primary_key")
            if "t" in table.columns:
                raise ShadowFailedError("column_named_t")  # would shadow the hash's row alias
        check_columns(self._policy, schema.columns_by_table())
        # Agent SQL may only name known tables of the public schema: a temp table or another
        # schema could stand in for the before-snapshot (D-137).
        if any(s != "public" for s in classification.schemas):
            raise ShadowFailedError("unknown_table")
        named = (*classification.tables, *classification.target_tables)
        if any(name not in schema.tables for name in named):
            raise ShadowFailedError("unknown_table")

    async def _measure(self, conn: asyncpg.Connection, classification: Classification) -> Diff:
        schema = await read_schema(conn)
        self._check(schema, classification)
        run = secrets.token_hex(4)
        names = sorted(schema.tables)
        index = {name: i for i, name in enumerate(names)}

        def temp(kind: str, table: str) -> str:
            return f"bp_{run}_{kind}{index[table]}"

        def key_sql(table: str) -> str:
            return pk_json_sql("t", schema.tables[table].pk_columns)

        def before_rel(table: str) -> str:
            """The table as it was before the write, rebuilt from the snapshot's row images."""
            return (
                f"(SELECT r.* FROM {temp('s', table)} s,"
                f" LATERAL jsonb_populate_record(NULL::{quote_ident(table)}, s.row) r)"
            )

        # 1. Snapshot every row of every table (key, hash and a row image), outside the
        # transaction. Temp tables survive it; the image lets the "before" side be read later
        # without sending any keys back to the copy (D-142).
        rows_before: dict[str, int] = {}
        for table in names:
            status = await conn.execute(
                f"CREATE TEMP TABLE {temp('s', table)} AS SELECT {key_sql(table)} AS pk,"
                f" {ROW_HASH_SQL} AS h, to_jsonb(t) AS row FROM {quote_ident(table)} t"
            )
            rows_before[table] = int(status.split()[-1])

        # 2. Run the write, diff, read both sides, then roll everything back. Everything the diff
        # produces lives in temp tables created inside the transaction.
        changed: dict[str, list[tuple[str, str | None, str]]] = {}
        tx = conn.transaction()
        await tx.start()
        try:
            try:
                await conn.fetch(classification.pinned_sql)
            except asyncpg.PostgresError as exc:
                logger.error("shadow_statement_error", sqlstate=exc.sqlstate)
                raise ShadowFailedError(f"statement_error_{exc.sqlstate}") from None
            for table in names:
                status = await conn.execute(
                    f"CREATE TEMP TABLE {temp('d', table)} AS"
                    f" SELECT coalesce(b.pk, a.pk) AS pk, b.h AS before_hash,"
                    f" CASE WHEN a.pk IS NULL THEN 'deleted' WHEN b.pk IS NULL THEN 'inserted'"
                    f" ELSE 'updated' END AS change"
                    f" FROM {temp('s', table)} b FULL OUTER JOIN"
                    f" (SELECT {key_sql(table)} AS pk, {ROW_HASH_SQL} AS h"
                    f" FROM {quote_ident(table)} t) a"
                    f" ON a.pk = b.pk WHERE b.h IS DISTINCT FROM a.h"
                )
                if int(status.split()[-1]):
                    rows = await conn.fetch(
                        f"SELECT pk::text AS pk, before_hash, change FROM {temp('d', table)}"
                    )
                    changed[table] = [(r["pk"], r["before_hash"], r["change"]) for r in rows]
            after = await self._side_info(
                conn, schema, changed, kinds=("inserted", "updated"), side="after", temp=temp
            )
            before = await self._side_info(
                conn,
                schema,
                changed,
                kinds=("deleted", "updated"),
                side="before",
                temp=temp,
                relation=before_rel,
            )
            cascades = await self._cascade_expectations(
                conn, schema, changed, classification, temp, before_rel
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await tx.rollback()
            raise
        await tx.rollback()
        return self._assemble(schema, classification, rows_before, changed, before, after, cascades)

    async def _side_info(
        self,
        conn: asyncpg.Connection,
        schema: CompanySchema,
        changed: Mapping[str, Sequence[tuple[str, str | None, str]]],
        *,
        kinds: tuple[str, ...],
        side: str,
        temp: Any,
        relation: Any = None,
    ) -> "_SideInfo":
        """Breakdowns, derived-group members and sample rows on one side of the change.

        `after` reads the live tables (inserted and updated rows); `before` reads the snapshot's
        row images (deleted and updated rows). Both run inside the transaction.
        """
        info = _SideInfo()
        for table, rows in changed.items():
            if not any(change in kinds for _, _, change in rows):
                continue
            diff = temp("d", table)
            source = relation(table) if relation else quote_ident(table)
            key = pk_json_sql("t", schema.tables[table].pk_columns)

            def member(*changes: str, key: str = key, diff: str = diff) -> str:
                listed = ", ".join(f"'{c}'" for c in changes)
                return f"{key} IN (SELECT pk FROM {diff} WHERE change IN ({listed}))"

            # After-values for inserted rows; before-values for deleted and updated rows. An
            # updated row is not counted again from its after state, so UPDATE ... SET plan = 'pro'
            # on free users touches no paying customer (the scenario files agree, D-139).
            scope = member(*kinds) if side == "before" else member("inserted")
            for column in self._policy.important_columns.get(table, []):
                counts = await conn.fetch(
                    f"SELECT t.{quote_ident(column)}::text AS value, count(*) AS n"
                    f" FROM {source} t WHERE {scope} GROUP BY 1"
                )
                bucket = info.breakdowns.setdefault(f"{table}.{column}", {})
                for r in counts:
                    label = "NULL" if r["value"] is None else mask_text(r["value"])
                    bucket[label] = bucket.get(label, 0) + r["n"]
            for name, group in self._policy.derived_groups.get(table, {}).items():
                found = await conn.fetch(
                    f"SELECT {key}::text AS pk FROM {source} t"
                    f" WHERE ({group.condition}) AND {scope}"
                )
                info.members.setdefault(f"{table}.{name}", set()).update(r["pk"] for r in found)
            listed_kinds = ", ".join(f"'{k}'" for k in kinds)
            samples = await conn.fetch(
                f"SELECT d.change, row_to_json(t)::text AS row FROM {source} t"
                f" JOIN {diff} d ON d.pk = {key}"
                f" WHERE d.change IN ({listed_kinds})"
                f" ORDER BY {key} LIMIT {SAMPLES_PER_SIDE}"
            )
            for r in samples:
                info.samples.append(
                    SampleRow(
                        table=table,
                        change=r["change"],
                        side=side,
                        values=mask_row(self._policy, table, json.loads(r["row"])),
                    )
                )
        return info

    async def _cascade_expectations(
        self,
        conn: asyncpg.Connection,
        schema: CompanySchema,
        changed: Mapping[str, Sequence[tuple[str, str | None, str]]],
        classification: Classification,
        temp: Any,
        before_rel: Any,
    ) -> list[CascadePath]:
        expectations: list[CascadePath] = []
        for target in classification.target_tables:
            rows = changed.get(target)
            if not rows or not any(change == "deleted" for _, _, change in rows):
                continue
            for path in fk_paths(schema.foreign_keys, target):
                sql = _cascade_count_sql(
                    path, schema.tables[target].pk_columns, temp("d", target), before_rel
                )
                count = await conn.fetchval(sql)
                if count:
                    expectations.append(
                        CascadePath(
                            target=target, edges=path, on_delete=path[-1].on_delete, count=count
                        )
                    )
        return expectations

    def _assemble(
        self,
        schema: CompanySchema,
        classification: Classification,
        rows_before: Mapping[str, int],
        changed: Mapping[str, Sequence[tuple[str, str | None, str]]],
        before: "_SideInfo",
        after: "_SideInfo",
        cascades: list[CascadePath],
    ) -> Diff:
        targets = set(classification.target_tables)
        tables: dict[str, TableDiff] = {}
        changes: list[RowChange] = []
        for name in sorted(schema.tables):
            rows = changed.get(name, [])
            counts = {kind: sum(1 for _, _, c in rows if c == kind) for kind in PREVIEW_CHANGES}
            tables[name] = TableDiff(
                table=name,
                rows_before=rows_before[name],
                is_cascade=bool(rows) and name not in targets,
                **counts,
            )
            changes.extend(
                RowChange(table=name, primary_key=json.loads(pk), before_hash=h, change=change)
                for pk, h, change in rows
            )
        breakdowns: dict[str, dict[str, int]] = {}
        for info in (before, after):
            for key, counts in info.breakdowns.items():
                bucket = breakdowns.setdefault(key, {})
                for label, n in counts.items():
                    bucket[label] = bucket.get(label, 0) + n
        # A row counts for a group by its state before the write (inserted rows: after it).
        derived = {
            key: len(before.members.get(key, set()) | after.members.get(key, set()))
            for key in {*before.members, *after.members}
        }
        for table, t in tables.items():
            if t.changed:
                for name in self._policy.derived_groups.get(table, {}):
                    derived.setdefault(f"{table}.{name}", 0)
        samples = sorted(
            [*before.samples, *after.samples],
            key=lambda s: (s.table not in targets, s.table, s.side != "before"),
        )
        return Diff(
            tables=tables,
            breakdowns=breakdowns,
            derived_groups=derived,
            cascade_expectations=tuple(cascades),
            samples=tuple(samples),
            changes=tuple(changes),
        )


@dataclass
class _SideInfo:
    breakdowns: dict[str, dict[str, int]] = field(default_factory=dict)
    members: dict[str, set[str]] = field(default_factory=dict)
    samples: list[SampleRow] = field(default_factory=list)
