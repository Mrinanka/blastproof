"""The pure half of the commit (backend plan step 16): no I/O, so every rule is table-testable.

* `commit_statement` turns the approved `pinned_sql` into the statement the commit runs: the same
  write, with the agent's own `RETURNING` replaced by one that reports, for every row, the old and
  new key and hash (PostgreSQL 18 `RETURNING WITH (OLD AS ..., NEW AS ...)`).
* `returned_diff` rebuilds the target table's changes from those rows the way the shadow run does.
* `find_drift` compares what the commit found and did with what the owner approved. Its result holds
  counts only: no key, no value, so it is safe to log, store in an event and show.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from app.services.company_schema import pk_json_sql, quote_ident, row_hash_sql

_TOKENIZER = Postgres.Tokenizer()
_DML = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.TruncateTable)
_ROOT_DML = (exp.Insert, exp.Update, exp.Delete, exp.Merge)


class CommitUnsupportedError(ValueError):
    """The write has a shape the commit cannot check, so it is refused. `reason` is a code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def canonical_key(value: str | Mapping[str, Any]) -> str:
    """A primary key as one comparable string, whether it came as jsonb text or as a parsed dict."""
    parsed = json.loads(value) if isinstance(value, str) else value
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


# --- The statement ------------------------------------------------------------------------------


def _without_agent_returning(sql: str) -> tuple[str, exp.Expression]:
    """`sql` cut before the agent's own RETURNING (and any trailing `;` or comment), and its AST.

    The cut is by tokenizer span, so a `'RETURNING'` string or a quoted identifier is never mistaken
    for the clause. The cut text is parsed again and must equal the original without its RETURNING;
    otherwise the cut is refused (the same self-check as the pinning of clock functions, D-122).
    """
    try:
        tokens = _TOKENIZER.tokenize(sql)
        parsed = sqlglot.parse(sql, read="postgres")
    except (SqlglotError, RecursionError):
        raise CommitUnsupportedError("unparseable") from None
    while tokens and tokens[-1].token_type == TokenType.SEMICOLON:
        tokens.pop()
    # A comment after the last `;` parses as a statement of its own.
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if (
        not tokens
        or len(statements) != 1
        or any(t.token_type == TokenType.SEMICOLON for t in tokens)
    ):
        raise CommitUnsupportedError("multiple_statements")
    statement = statements[0]

    depth = 0
    cut_at = len(tokens)
    for i, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
        elif token.token_type == TokenType.RETURNING and depth == 0:
            cut_at = i
            break
    if cut_at == 0:
        raise CommitUnsupportedError("unparseable")
    cut = sql[: tokens[cut_at - 1].end + 1]

    original = statement.copy()
    original.set("returning", None)
    try:
        again = sqlglot.parse(cut, read="postgres")
    except (SqlglotError, RecursionError):
        raise CommitUnsupportedError("cut_changed") from None
    again = [s for s in again if s is not None and not isinstance(s, exp.Semicolon)]
    if len(again) != 1 or again[0] != original:
        raise CommitUnsupportedError("cut_changed")
    return cut, statement


def _check_shape(statement: exp.Expression) -> None:
    root = statement
    if not isinstance(root, _ROOT_DML):
        raise CommitUnsupportedError("not_a_plain_write")
    # A MERGE holds its UPDATE/INSERT/DELETE actions in WHEN clauses: they are the same write.
    nested = [
        node
        for node in root.find_all(*_DML)
        if node is not root and node.find_ancestor(exp.When) is None
    ]
    if nested:
        raise CommitUnsupportedError("data_modifying_subquery")
    target = root.this.this if isinstance(root.this, exp.Schema) else root.this
    if not isinstance(target, exp.Table):
        raise CommitUnsupportedError("no_single_target")


def commit_statement(pinned_sql: str, pk_columns: Sequence[str], aliases: tuple[str, str]) -> str:
    """The approved write with a RETURNING that reports each row's old and new key and hash.

    Columns of one returned row: old key, new key, old hash, new hash (jsonb or text, NULL on the
    side that has no row), then the old and new row as jsonb for the before-images. `aliases` are
    the OLD and NEW names, random per commit so they cannot clash with the agent's own.
    """
    if not pk_columns:
        raise CommitUnsupportedError("no_primary_key")
    cut, statement = _without_agent_returning(pinned_sql)
    _check_shape(statement)
    old, new = (quote_ident(a) for a in aliases)
    first = quote_ident(pk_columns[0])

    def side(alias: str, expression: str) -> str:
        # Primary keys are NOT NULL, so a NULL key means this side has no row (INSERT has no OLD,
        # DELETE has no NEW).
        return f"CASE WHEN {alias}.{first} IS NULL THEN NULL ELSE {expression} END"

    outputs = ", ".join(
        [
            side(old, pk_json_sql(aliases[0], pk_columns)),
            side(new, pk_json_sql(aliases[1], pk_columns)),
            side(old, row_hash_sql(old)),
            side(new, row_hash_sql(new)),
            side(old, f"to_jsonb({old})"),
            side(new, f"to_jsonb({new})"),
        ]
    )
    return f"{cut}\nRETURNING WITH (OLD AS {old}, NEW AS {new}) {outputs}"


# --- What the statement returned ----------------------------------------------------------------


@dataclass(frozen=True)
class ReturnedRow:
    """One row of the commit statement's RETURNING; keys are canonical (`canonical_key`)."""

    old_key: str | None
    new_key: str | None
    old_hash: str | None
    new_hash: str | None


@dataclass(frozen=True)
class ReturnedDiff:
    deleted: frozenset[str]
    updated: frozenset[str]
    inserted: int

    @property
    def rows(self) -> int:
        return len(self.deleted) + len(self.updated) + self.inserted


def returned_diff(rows: Iterable[ReturnedRow]) -> ReturnedDiff:
    """The target table's changes, as the shadow run counts them: an UPDATE that changed nothing is
    not a change, and one that moved a row to another key is a delete plus an insert."""
    deleted: set[str] = set()
    updated: set[str] = set()
    inserted = 0
    for row in rows:
        if row.old_key is not None and row.new_key is None:
            deleted.add(row.old_key)
        elif row.old_key is None and row.new_key is not None:
            inserted += 1
        elif row.old_key is not None and row.new_key is not None:
            if row.old_key != row.new_key:
                deleted.add(row.old_key)
                inserted += 1
            elif row.old_hash != row.new_hash:
                updated.add(row.old_key)
    return ReturnedDiff(frozenset(deleted), frozenset(updated), inserted)


# --- Drift --------------------------------------------------------------------------------------


def cascade_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    """Identifies one cascade path of `actions.cascade_expectations`: the table at its far end, the
    tables it passes through, and what the foreign key does."""
    return (item["table"], *item["via"], f"on_delete={item['on_delete']}")


@dataclass(frozen=True)
class Expected:
    """What the owner approved, from `preview_rows`, `impact` and `cascade_expectations`."""

    deleted: Mapping[str, str]  # key -> hash before the write
    updated: Mapping[str, str]
    inserted: int
    cascades: Mapping[tuple[str, ...], int]

    @property
    def locked(self) -> dict[str, str]:
        """The rows the commit locks and compares: every deleted and updated key with its hash."""
        return {**self.updated, **self.deleted}


@dataclass(frozen=True)
class Drift:
    """Why the real database no longer matches the preview. Counts only: no key, no value."""

    reason: str  # rows | keys | inserts | cascades
    counts: dict[str, int]


def find_drift(
    expected: Expected,
    hashes: Mapping[str, str],
    cascades: Mapping[tuple[str, ...], int],
    returned: ReturnedDiff | None = None,
) -> Drift | None:
    """None when everything matches. `hashes` are the locked rows found now (key -> hash) and
    `cascades` the cascade counts now; `returned` is the statement's result, or None to check
    only what can be checked before running it."""
    counts: dict[str, int] = {}
    locked = expected.locked
    missing = sum(1 for key in locked if key not in hashes)
    changed = sum(1 for key, h in locked.items() if key in hashes and hashes[key] != h)
    if missing:
        counts["rows_missing"] = missing
    if changed:
        counts["rows_changed"] = changed
    reason = "rows" if counts else ""

    paths = set(expected.cascades) | set(cascades)
    cascade_diff = sum(1 for p in paths if expected.cascades.get(p, 0) != cascades.get(p, 0))
    if cascade_diff:
        counts["cascade_paths_changed"] = cascade_diff
        reason = reason or "cascades"

    if returned is not None:
        approved_deleted, approved_updated = set(expected.deleted), set(expected.updated)
        for name, want, got in (
            ("deleted", approved_deleted, set(returned.deleted)),
            ("updated", approved_updated, set(returned.updated)),
        ):
            if want - got:
                counts[f"{name}_missing"] = len(want - got)
            if got - want:
                counts[f"{name}_extra"] = len(got - want)
        if any(k.startswith(("deleted_", "updated_")) for k in counts):
            reason = reason or "keys"
        if returned.inserted != expected.inserted:
            counts["inserted_expected"] = expected.inserted
            counts["inserted_returned"] = returned.inserted
            reason = reason or "inserts"
    return Drift(reason, counts) if counts else None
