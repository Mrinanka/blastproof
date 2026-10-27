"""The real commit (backend plan step 16): apply an approved write to the company database, but only
if the database still matches what the owner saw.

One company transaction does all of it, and nothing is applied unless every check passes:

1. insert the action's marker in `blastproof.applied_actions` (a second commit of the same action
   finds it and changes nothing: commits are idempotent);
2. lock the approved rows and compare each row's hash with the preview;
3. count the cascades (what the foreign keys would remove or null) and compare with the preview;
4. run `pinned_sql` with a RETURNING that reports every changed row, rebuild the changes from it
   and compare keys and counts with the preview;
5. save the target table's before-images to Blastproof (own transaction, committed first), then
   COMMIT.

Any difference rolls back and raises `CommitDriftError` (the graph previews again, once). A crash or
a dropped COMMIT leaves the marker as the witness: it decides whether the write happened. Logs carry
codes and counts only, never SQL, keys or values.
"""

import asyncio
import contextlib
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.clock import Clock
from app.db.before_images import (
    CommitInputs,
    SavedImage,
    commit_inputs,
    count_before_images,
    delete_before_images,
    previewed_keys,
    replace_before_images,
)
from app.services import company_db
from app.services.classifier import Kind, classify
from app.services.commit import (
    CommitDriftError,
    CommitFailedError,
    CommitOutcome,
    CommitOutcomeUnknownError,
)
from app.services.commit_sql import (
    CommitUnsupportedError,
    Expected,
    ReturnedRow,
    canonical_key,
    cascade_key,
    commit_statement,
    find_drift,
    returned_diff,
)
from app.services.company_schema import pk_json_sql, quote_ident, read_schema, row_hash_sql
from app.services.shadow import CascadePath, cascade_count_sql, fk_paths

logger = structlog.get_logger()

LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "30s"
MARKER_CHECK_PAUSES = (1.0, 2.0, 4.0)  # seconds before the 1st, 2nd and 3rd look at the marker
ATTEMPTS = 2  # the whole commit runs again once when the first try left no trace

# A test seam: called with a stage name (`start`, `before_commit`, `after_commit`) and the company
# transaction (None at `start`). The default does nothing.
Fault = Callable[[str, Any], Awaitable[None]]

_LOCK_NOT_AVAILABLE = "55P03"
_QUERY_CANCELED = "57014"


class _OutcomeUnknownError(Exception):
    """Internal: COMMIT raised something that does not say whether it happened."""


class CompanyCommitter:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Clock,
        *,
        fault: Fault | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._clock = clock
        self._fault = fault
        self._sleep = sleep

    # --- Entry ----------------------------------------------------------------------------------

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        with structlog.contextvars.bound_contextvars(action_id=str(action_id)):
            if self._fault is not None:
                await self._fault("start", None)
            async with self._sessionmaker() as session:
                inputs = await commit_inputs(session, action_id)
            if inputs is None or not inputs.pinned_sql:
                raise CommitFailedError("commit_not_found")
            # Look the writer URL up for the system's own profile. There is no fallback to the
            # other profile's URL (BE-97): an action must never be committed to the wrong database.
            url = company_db.writer_url_for(inputs.profile, self._settings)
            if url is None:
                logger.error("commit_not_configured", profile=inputs.profile)
                raise CommitFailedError("commit_not_configured")
            target = self._target(inputs)
            async with self._sessionmaker() as session:
                deleted, updated = await previewed_keys(session, action_id, target)
            expected = Expected(
                deleted=deleted,
                updated=updated,
                inserted=int(
                    inputs.impact.get("table_changes", {}).get(target, {}).get("inserted", 0)
                ),
                cascades={cascade_key(c): int(c["count"]) for c in inputs.cascade_expectations},
            )
            for attempt in range(ATTEMPTS):
                try:
                    return await self._attempt(action_id, inputs, target, expected)
                except _OutcomeUnknownError:
                    found = await self._marker_present(url, action_id)
                    if found is None:
                        logger.error("commit_outcome_unknown")
                        raise CommitOutcomeUnknownError from None
                    if found:
                        logger.info("commit_reconciled", attempt=attempt + 1)
                        return await self._settled(action_id, already_applied=False)
                    logger.warning("commit_left_no_trace", attempt=attempt + 1)
            logger.error("commit_outcome_unknown")
            raise CommitOutcomeUnknownError

    def _target(self, inputs: CommitInputs) -> str:
        """The one table the write changes, from the pinned SQL itself (pure)."""
        classification = classify(inputs.pinned_sql, now=self._clock.now())
        if classification.kind != Kind.WRITE or len(classification.target_tables) != 1:
            raise CommitFailedError("commit_unsupported")
        return classification.target_tables[0]

    # --- One try --------------------------------------------------------------------------------

    def _clone_lock(self, profile: str, url: str) -> AbstractAsyncContextManager[None]:
        """A clone of `company_main` (the local profile, or NEON_CLIENT=fake) needs every other
        session on it gone, so a commit holds the shared lock the reads hold. It only has to keep
        clones out, so it is shared, not exclusive (D-199)."""
        if profile == "local" or self._settings.neon_client == "fake":
            return company_db.advisory_lock(url, shared=True)
        return contextlib.nullcontext()

    async def _attempt(
        self, action_id: uuid.UUID, inputs: CommitInputs, target: str, expected: Expected
    ) -> CommitOutcome:
        url = company_db.writer_url_for(inputs.profile, self._settings)
        assert url is not None
        try:
            async with (
                self._clone_lock(inputs.profile, url),
                company_db.connect_writer_for(inputs.profile, self._settings) as conn,
            ):
                tx = conn.transaction()
                await tx.start()
                try:
                    return await self._in_transaction(conn, tx, action_id, inputs, target, expected)
                except BaseException:
                    with contextlib.suppress(Exception):
                        await tx.rollback()
                    raise
        except company_db.CompanyDbUnavailableError as exc:
            missing = str(exc) == "not_configured"
            reason = "commit_not_configured" if missing else "commit_unavailable"
            raise CommitFailedError(reason) from None
        except _OutcomeUnknownError:
            raise  # the before-images stay: the write may be in the company database
        except (CommitDriftError, CommitFailedError):
            # Nothing was applied, so images a crashed earlier try left are stale: remove them.
            await self._drop_images(action_id)
            raise
        except asyncpg.PostgresError as exc:
            await self._drop_images(action_id)
            logger.error("commit_statement_error", sqlstate=exc.sqlstate)
            raise CommitFailedError(self._statement_reason(exc.sqlstate)) from None
        except (OSError, asyncpg.InterfaceError, TimeoutError) as exc:
            await self._drop_images(action_id)
            logger.error("commit_connection_lost", error_class=type(exc).__name__)
            raise CommitFailedError("commit_connection_lost") from None

    @staticmethod
    def _statement_reason(sqlstate: str | None) -> str:
        if sqlstate == _LOCK_NOT_AVAILABLE:
            return "commit_lock_timeout"
        if sqlstate == _QUERY_CANCELED:
            return "commit_timeout"
        return f"commit_statement_error_{sqlstate}"

    async def _in_transaction(
        self,
        conn: asyncpg.Connection,
        tx: Any,
        action_id: uuid.UUID,
        inputs: CommitInputs,
        target: str,
        expected: Expected,
    ) -> CommitOutcome:
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")

        marker = await conn.fetchval(
            "INSERT INTO blastproof.applied_actions (action_id) VALUES ($1)"
            " ON CONFLICT DO NOTHING RETURNING action_id",
            action_id,
        )
        if marker is None:
            await tx.rollback()
            logger.info("commit_already_applied")
            return await self._settled(action_id, already_applied=True)

        schema = await read_schema(conn)
        table = schema.tables.get(target)
        if table is None or not table.pk_columns:
            raise CommitFailedError("commit_unsupported")
        aliases = (f"bp_o_{secrets.token_hex(4)}", f"bp_n_{secrets.token_hex(4)}")
        try:
            statement = commit_statement(inputs.pinned_sql, table.pk_columns, aliases)
        except CommitUnsupportedError as exc:
            logger.error("commit_unsupported", reason_code=exc.reason)
            raise CommitFailedError("commit_unsupported") from None

        hashes = await self._lock_and_hash(conn, target, table.pk_columns, expected)
        cascades = await self._cascades(conn, schema, target, table.pk_columns, expected)
        self._raise_on_drift(expected, hashes, cascades, None)

        records = await conn.fetch(statement)
        rows = [
            ReturnedRow(
                None if r[0] is None else canonical_key(r[0]),
                None if r[1] is None else canonical_key(r[1]),
                r[2],
                r[3],
            )
            for r in records
        ]
        returned = returned_diff(rows)
        self._raise_on_drift(expected, hashes, cascades, returned)

        images = self._images(target, records, rows)
        async with self._sessionmaker.begin() as session:
            await replace_before_images(session, action_id, images)

        await self._finish(tx, action_id)
        logger.info("commit_done", rows=returned.rows)
        return CommitOutcome(rows=returned.rows, stub=False)

    async def _finish(self, tx: Any, action_id: uuid.UUID) -> None:
        """COMMIT, and say what an error from it means. A server error (not a connection one) means
        the transaction is rolled back and nothing was applied; anything else does not say."""
        try:
            if self._fault is not None:
                await self._fault("before_commit", tx)
            await tx.commit()
            if self._fault is not None:
                await self._fault("after_commit", tx)
        except asyncpg.PostgresError as exc:
            if (exc.sqlstate or "").startswith("08"):
                raise _OutcomeUnknownError from None
            logger.error("commit_error", sqlstate=exc.sqlstate)
            await self._drop_images(action_id)
            raise CommitFailedError(f"commit_error_{exc.sqlstate}") from None
        except Exception:
            raise _OutcomeUnknownError from None

    # --- The checks -----------------------------------------------------------------------------

    @staticmethod
    async def _lock_and_hash(
        conn: asyncpg.Connection, target: str, pk: tuple[str, ...], expected: Expected
    ) -> dict[str, str]:
        """Lock every approved row and read its hash now. A row that is gone is left out."""
        locked = expected.locked
        if not locked:
            return {}
        keys = json.dumps([json.loads(k) for k in locked])
        key_sql = pk_json_sql("t", pk)
        records = await conn.fetch(
            f"SELECT {key_sql} AS k, {row_hash_sql('t')} AS h FROM {quote_ident(target)} t"
            f" JOIN jsonb_array_elements($1::jsonb) q(k) ON q.k = {key_sql} FOR UPDATE OF t",
            keys,
        )
        return {canonical_key(r["k"]): r["h"] for r in records}

    @staticmethod
    async def _cascades(
        conn: asyncpg.Connection,
        schema: Any,
        target: str,
        pk: tuple[str, ...],
        expected: Expected,
    ) -> dict[tuple[str, ...], int]:
        if not expected.deleted:
            return {}
        keys = json.dumps([json.loads(k) for k in expected.deleted])
        found: dict[tuple[str, ...], int] = {}
        for path in fk_paths(schema.foreign_keys, target):
            sql = cascade_count_sql(
                path, pk, "SELECT k FROM jsonb_array_elements($1::jsonb) k", quote_ident
            )
            count = await conn.fetchval(sql, keys)
            if count:
                item = CascadePath(target, path, path[-1].on_delete, count).to_json()
                found[cascade_key(item)] = count
        return found

    @staticmethod
    def _raise_on_drift(
        expected: Expected,
        hashes: dict[str, str],
        cascades: dict[tuple[str, ...], int],
        returned: Any,
    ) -> None:
        drift = find_drift(expected, hashes, cascades, returned)
        if drift is not None:
            logger.warning("commit_drift", reason=drift.reason, **drift.counts)
            raise CommitDriftError(drift.reason, drift.counts)

    @staticmethod
    def _images(target: str, records: list[Any], rows: list[ReturnedRow]) -> list[SavedImage]:
        """Before-images of the rows that changed (a no-op update is not a change)."""
        images: list[SavedImage] = []
        for record, row in zip(records, rows, strict=True):
            if (
                row.old_key is not None
                and row.new_key == row.old_key
                and row.old_hash == row.new_hash
            ):
                continue
            images.append(
                SavedImage(
                    table=target,
                    primary_key=row.old_key or row.new_key or "{}",
                    row_before=record[4],
                    row_after=record[5],
                )
            )
        return images

    # --- After a crash or a dropped COMMIT ------------------------------------------------------

    async def _marker_present(self, url: str, action_id: uuid.UUID) -> bool | None:
        """Whether the marker is in the company database: True/False, or None when it cannot be
        reached. Three looks on a fresh connection, 1, 2 and 4 seconds apart."""
        for pause in MARKER_CHECK_PAUSES:
            await self._sleep(pause)
            try:
                async with company_db.open_connection(dsn=url) as conn:
                    return bool(
                        await conn.fetchval(
                            "SELECT EXISTS (SELECT 1 FROM blastproof.applied_actions"
                            " WHERE action_id = $1)",
                            action_id,
                        )
                    )
            except Exception as exc:
                logger.warning("commit_marker_check_failed", error_class=type(exc).__name__)
        return None

    async def _settled(self, action_id: uuid.UUID, *, already_applied: bool) -> CommitOutcome:
        """The outcome of a commit that is already in the company database: `rows` is what the
        before-images say it changed."""
        async with self._sessionmaker() as session:
            rows = await count_before_images(session, action_id)
        return CommitOutcome(rows=rows, stub=False, already_applied=already_applied)

    async def _drop_images(self, action_id: uuid.UUID) -> None:
        """Best effort: nothing was applied, so there is nothing to undo later."""
        try:
            async with self._sessionmaker.begin() as session:
                await delete_before_images(session, action_id)
        except Exception as exc:
            logger.error("before_images_delete_failed", error_class=type(exc).__name__)
