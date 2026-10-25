"""The SQL classifier (backend plan step 8): what a statement is, what it touches, and how risky.

It never decides anything; it returns facts for the pass-1 rules (step 9). It fails closed: input
it cannot understand is `unknown`, which the rules block.

Two design points worth knowing (D-122, D-123):
- `pinned_sql` is the original text with each clock function replaced by one UTC literal. The
  replacement splices the tokenizer's character spans, because regenerating SQL from sqlglot's tree
  changes it (`current_time` becomes `CURRENT_TIME()`, which Postgres rejects). The result is parsed
  again and must classify the same, or the statement becomes `unknown`.
- `sql_display` is `pinned_sql` with personal data masked, also by token spans.
"""

import asyncio
import dataclasses
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import partial
from typing import Final

import sqlglot
import structlog
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.simplify import simplify
from sqlglot.tokens import Token, TokenType

from app.core.clock import Clock, require_aware_utc
from app.services.masking import (
    MASKED_PLACEHOLDER,
    SENSITIVE_NAME_RE,
    mask_text,
    pii_placeholder,
)
from app.services.sql_functions import (
    PINNED_TIME_FUNCTIONS,
    SAFE_ANONYMOUS_FUNCTIONS,
    SPECIAL_DATETIME_STRINGS,
    VOLATILE_FUNCTIONS,
    is_side_effect_function,
)

logger = structlog.get_logger()

MAX_SQL_BYTES: Final = 20 * 1024
CLASSIFY_TIMEOUT_SECONDS: Final = 2.0
# A WHERE with more nodes than this is not run through `simplify`, which is roughly quadratic
# (a 20 KB OR chain took 39 s while holding the GIL).
MAX_SIMPLIFY_NODES: Final = 500
FAST_LANE_MAX_ROWS: Final = 10

# Parsing runs here, apart from the default executor that argon2 uses, so a runaway parse that
# outlives its 2 s cap cannot starve logins.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="classifier")


class Kind(StrEnum):
    READ = "read"
    WRITE = "write"
    DDL = "ddl"
    UNKNOWN = "unknown"


_SEVERITY = {Kind.READ: 0, Kind.WRITE: 1, Kind.DDL: 2, Kind.UNKNOWN: 3}


@dataclass(frozen=True)
class Classification:
    kind: Kind
    # Why the kind is unknown: too_long, empty, unparseable, unsupported_statement, parse_timeout,
    # check_error or pin_check_failed.
    reason: str | None = None
    # One value for `actions.statement_type`: the root's type; for a read that hides DML, the first
    # DML type; for several statements, the first that is not transaction control.
    statement_type: str | None = None
    # Every statement type in the tree, for example ("delete", "select").
    statement_types: tuple[str, ...] = ()
    # Sorted. "x" for the public schema or no schema, "schema.x" otherwise. No CTE names and no
    # table functions. Unquoted names are lower case (Postgres folds them).
    tables: tuple[str, ...] = ()
    # Tables written by INSERT, UPDATE, DELETE, MERGE and TRUNCATE.
    target_tables: tuple[str, ...] = ()
    # Unqualified means "public". Includes the schemas of qualified functions.
    schemas: tuple[str, ...] = ()
    # Every DELETE and UPDATE in the tree has a WHERE that is not a constant true.
    has_where: bool = True
    multi_statement: bool = False
    transaction_control: bool = False
    # A clock function, a volatile function, a special datetime string or an unknown function.
    non_deterministic: bool = False
    # A volatile value somewhere a preview could not repeat at commit (see `_flag_volatile`).
    volatile_in_where: bool = False
    # LIMIT, OFFSET or FETCH without ORDER BY on the same node, inside a write.
    unordered_limit: bool = False
    fast_lane_eligible: bool = False
    side_effect_functions: tuple[str, ...] = ()
    unknown_functions: tuple[str, ...] = ()
    pinned_sql: str = ""
    # {"now()": "2026-10-01T00:00:00Z", "current_date": "2026-10-01"}
    pinned_values: dict[str, str] = field(default_factory=dict)
    sql_display: str = ""


# Fields that must be identical before and after pinning (all but non_deterministic, the SQL
# text and the values).
_PIN_CHECKED_FIELDS = (
    "kind",
    "statement_type",
    "statement_types",
    "tables",
    "target_tables",
    "schemas",
    "has_where",
    "multi_statement",
    "transaction_control",
    "volatile_in_where",
    "unordered_limit",
    "fast_lane_eligible",
    "side_effect_functions",
    "unknown_functions",
)


# --- Parsing -----------------------------------------------------------------------------------

_TOKENIZER = Postgres.Tokenizer()

_STRING_TOKEN_TYPES = frozenset(
    getattr(TokenType, name)
    for name in (
        "STRING",
        "NATIONAL_STRING",
        "RAW_STRING",
        "HEREDOC_STRING",
        "BYTE_STRING",
        "HEX_STRING",
        "BIT_STRING",
        "UNICODE_STRING",
    )
    if hasattr(TokenType, name)
)
_NAME_TOKEN_TYPES = frozenset({TokenType.VAR, TokenType.IDENTIFIER})

# Leading keywords of transaction control. sqlglot parses several of these as plain expressions
# (`START TRANSACTION`, `SAVEPOINT a`, `ABORT`), so they are detected from the tokens.
_TRANSACTION_WORDS = frozenset(
    {"BEGIN", "START", "COMMIT", "END", "ROLLBACK", "ABORT", "SAVEPOINT", "RELEASE"}
)
_TRANSACTION_TYPE = {
    "BEGIN": "begin",
    "START": "begin",
    "COMMIT": "commit",
    "END": "commit",
    "ROLLBACK": "rollback",
    "ABORT": "rollback",
    "SAVEPOINT": "savepoint",
    "RELEASE": "savepoint",
    "PREPARE": "prepare",
    "SET": "set",
}


def _transaction_type(group: list[Token]) -> str | None:
    """The type of a transaction-control statement, or None if the statement is not one."""
    words = [t.text.upper() for t in group[:3]]
    first = words[0]
    if first in _TRANSACTION_WORDS:
        return _TRANSACTION_TYPE[first]
    if first == "PREPARE" and words[1:2] == ["TRANSACTION"]:
        return "prepare"
    if first == "SET" and (
        words[1:2] == ["TRANSACTION"] or words[1:3] == ["SESSION", "CHARACTERISTICS"]
    ):
        return "set"
    return None


def _split_statements(tokens: list[Token]) -> list[list[Token]]:
    groups: list[list[Token]] = [[]]
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            groups.append([])
        else:
            groups[-1].append(token)
    return [g for g in groups if g]


def _norm(identifier: exp.Expression | None) -> str:
    """An identifier as Postgres sees it: quoted names keep their case, others fold to lower."""
    if not isinstance(identifier, exp.Identifier):
        return ""
    return identifier.name if identifier.args.get("quoted") else identifier.name.lower()


def _table_ident(table: exp.Table) -> str | None:
    name = table.args.get("this")
    return _norm(name) if isinstance(name, exp.Identifier) else None


def _table_schema(table: exp.Table) -> str:
    return _norm(table.args.get("db")) or "public"


def _table_label(table: exp.Table) -> str | None:
    name = _table_ident(table)
    if not name:
        return None
    schema = _table_schema(table)
    return name if schema == "public" else f"{schema}.{name}"


def _is_cte_reference(table: exp.Table) -> bool:
    """True if this table name refers to a CTE rather than a real table.

    A CTE body sees only the CTEs defined before it (or itself, in WITH RECURSIVE), so
    `WITH users AS (SELECT * FROM users)` still reads the real `users`.
    """
    if table.args.get("db"):
        return False
    name = _table_ident(table)
    node: exp.Expression = table
    while node.parent is not None:
        parent = node.parent
        if isinstance(parent, exp.With):
            ctes = parent.expressions
            index = next((i for i, c in enumerate(ctes) if c is node), len(ctes))
            visible = ctes[: index + 1] if parent.args.get("recursive") else ctes[:index]
            if any(c.alias_or_name.lower() == name for c in visible):
                return True
        else:
            with_ = parent.args.get("with_")
            if (
                isinstance(with_, exp.With)
                and node is not with_
                and any(c.alias_or_name.lower() == name for c in with_.expressions)
            ):
                return True
        node = parent
    return False


# --- Per-statement analysis --------------------------------------------------------------------

_TYPE_NAMES: dict[type[exp.Expression], str] = {
    exp.Select: "select",
    exp.SetOperation: "select",
    exp.Insert: "insert",
    exp.Update: "update",
    exp.Delete: "delete",
    exp.Merge: "merge",
    exp.TruncateTable: "truncate",
    exp.Create: "create",
    exp.Drop: "drop",
    exp.Alter: "alter",
    exp.Grant: "grant",
    exp.Revoke: "revoke",
    exp.Comment: "comment",
}
# Every value `Classification.statement_types` can hold, for the policy loader to check rule files.
STATEMENT_TYPES: Final = frozenset(_TYPE_NAMES.values()) | frozenset(_TRANSACTION_TYPE.values())
_DML = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.TruncateTable)
_DDL = (exp.Create, exp.Drop, exp.Alter, exp.Grant, exp.Revoke, exp.Comment)
_UNSUPPORTED = (exp.Command, exp.Set, exp.Copy, exp.Lock, exp.Transaction)
_TIME_NODES = (
    exp.CurrentTimestamp,
    exp.CurrentDate,
    exp.CurrentTime,
    exp.Localtimestamp,
    exp.Localtime,
)
_VOLATILE_NODES = (exp.Rand, exp.Uuid)


def _type_name(node: exp.Expression) -> str | None:
    for cls, name in _TYPE_NAMES.items():
        if isinstance(node, cls):
            return name
    return None


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def _qualifier(node: exp.Expression) -> list[str]:
    """Schema names written before a function: `pg_catalog.now()` gives ["pg_catalog"]."""
    parent = node.parent
    if isinstance(parent, exp.Dot) and parent.expression is node:
        return [_norm(i) for i in parent.this.find_all(exp.Identifier)]
    return []


def _is_special_datetime(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Cast | exp.TryCast):
        return False
    inner = node.this
    return (
        isinstance(inner, exp.Literal)
        and inner.is_string
        and inner.name.strip().lower() in SPECIAL_DATETIME_STRINGS
    )


@dataclass
class _Acc:
    """Facts gathered from all statements of one input."""

    kind: Kind = Kind.READ
    reason: str | None = None
    statement_type: str | None = None
    types: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    targets: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)
    has_where: bool = True
    transaction_control: bool = False
    non_deterministic: bool = False
    volatile_in_where: bool = False
    unordered_limit: bool = False
    side_effects: set[str] = field(default_factory=set)
    unknown_functions: set[str] = field(default_factory=set)
    time_functions_left: bool = False

    def raise_kind(self, kind: Kind, reason: str | None = None) -> None:
        if _SEVERITY[kind] > _SEVERITY[self.kind]:
            self.kind = kind
            self.reason = reason if kind == Kind.UNKNOWN else None


def _is_constant_true(condition: exp.Expression) -> bool:
    result = simplify(condition.copy(), dialect="postgres")
    return isinstance(result, exp.Boolean) and bool(result.this)


def _node_count(node: exp.Expression) -> int:
    return sum(1 for _ in node.find_all(exp.Expression))


def _or_terms(condition: exp.Expression) -> list[exp.Expression]:
    terms: list[exp.Expression] = []
    stack = [condition]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Paren):
            stack.append(node.this)
        elif isinstance(node, exp.Or):
            stack.extend((node.this, node.expression))
        else:
            terms.append(node)
    return terms


def _where_is_constant_true(where: exp.Where) -> bool:
    condition = where.this
    if _node_count(condition) <= MAX_SIMPLIFY_NODES:
        return _is_constant_true(condition)
    # Too big to simplify whole. A top-level OR term with no column or subquery can still make
    # the whole condition true; the measured fraction in pass 2 catches what this misses.
    return any(
        not term.find(exp.Column, exp.Select, exp.Subquery)
        and _node_count(term) <= MAX_SIMPLIFY_NODES
        and _is_constant_true(term)
        for term in _or_terms(condition)
    )


def _as_table(node: exp.Expression | None) -> exp.Table | None:
    if isinstance(node, exp.Schema):
        node = node.this
    return node if isinstance(node, exp.Table) else None


def _target_tables(node: exp.Expression) -> list[exp.Table]:
    if isinstance(node, exp.TruncateTable):
        candidates: Iterable[exp.Expression | None] = node.expressions
    else:
        candidates = [node.this]
    return [t for t in map(_as_table, candidates) if t is not None]


def _allowed_volatile_roots(dml: exp.Expression) -> list[exp.Expression]:
    """The parts of a DML statement where a volatile value is fine.

    Fine: the right-hand side of a SET, the VALUES rows or top-level SELECT list of an INSERT, and
    RETURNING. Anywhere else (WHERE, USING, ON, a subquery feeding them) the rows chosen at
    preview would differ at commit.
    """
    allowed: list[exp.Expression] = []
    returning = dml.args.get("returning")
    if returning is not None:
        allowed.append(returning)
    if isinstance(dml, exp.Update):
        allowed.extend(e.expression for e in dml.expressions if isinstance(e, exp.EQ))
    elif isinstance(dml, exp.Insert):
        source = dml.expression
        if isinstance(source, exp.Values | exp.Tuple):
            allowed.append(source)
        elif isinstance(source, exp.Select):
            allowed.extend(source.expressions)
    return allowed


def _flag_volatile(volatile_nodes: list[exp.Expression]) -> bool:
    for node in volatile_nodes:
        dml = node.find_ancestor(exp.Insert, exp.Update, exp.Delete, exp.Merge)
        if dml is None:
            continue
        allowed = _allowed_volatile_roots(dml)
        ancestors = {id(a) for a in _ancestors(node, dml)}
        if not any(id(root) in ancestors for root in allowed):
            return True
    return False


def _ancestors(node: exp.Expression, stop: exp.Expression) -> Iterable[exp.Expression]:
    current: exp.Expression | None = node
    while current is not None and current is not stop:
        yield current
        current = current.parent


def _has_unordered_limit(dml: exp.Expression) -> bool:
    return any(
        any(node.args.get(key) for key in ("limit", "offset", "fetch"))
        and not node.args.get("order")
        for node in dml.find_all(exp.Expression)
    )


def _scan_functions(root: exp.Expression, acc: _Acc) -> list[exp.Expression]:
    """Record every function in `root`; return the nodes whose value changes between runs."""
    volatile: list[exp.Expression] = []
    for node in root.find_all(exp.Func):
        if isinstance(node, exp.Cast | exp.TryCast):
            if _is_special_datetime(node):
                volatile.append(node)
                acc.non_deterministic = True
            continue
        if isinstance(node, _TIME_NODES):
            acc.non_deterministic = True
            acc.time_functions_left = True
            continue
        name = _function_name(node)
        qualifier = _qualifier(node)
        foreign = bool(qualifier) and qualifier[-1] != "pg_catalog"
        if qualifier and not (name in PINNED_TIME_FUNCTIONS and not foreign):
            # `pg_catalog.now()` is the real clock; its prefix goes away when it is pinned.
            acc.schemas.add(qualifier[-1])
        if isinstance(node, exp.Anonymous):
            if name in PINNED_TIME_FUNCTIONS:
                acc.non_deterministic = True
                acc.time_functions_left = True
                if foreign:
                    acc.unknown_functions.add(name)
                continue
            if is_side_effect_function(name):
                acc.side_effects.add(name)
                volatile.append(node)
            elif foreign or (
                name not in SAFE_ANONYMOUS_FUNCTIONS and name not in VOLATILE_FUNCTIONS
            ):
                acc.unknown_functions.add(name)
                volatile.append(node)
            elif name in VOLATILE_FUNCTIONS:
                volatile.append(node)
            continue
        if is_side_effect_function(name):
            acc.side_effects.add(name)
        if (
            isinstance(node, _VOLATILE_NODES)
            or name in VOLATILE_FUNCTIONS
            or name in acc.side_effects
        ):
            volatile.append(node)
    if volatile:
        acc.non_deterministic = True
    return volatile


def _fast_lane_value_ok(value: exp.Expression) -> bool:
    if isinstance(value, exp.Null | exp.Boolean):
        return True
    if isinstance(value, exp.Literal):
        return not (value.is_string and value.name.strip().lower() in SPECIAL_DATETIME_STRINGS)
    if isinstance(value, exp.Var):
        return value.name.upper() == "DEFAULT"
    if isinstance(value, exp.Neg):
        return isinstance(value.this, exp.Literal) and not value.this.is_string
    if isinstance(value, exp.Paren):
        return _fast_lane_value_ok(value.this)
    if isinstance(value, exp.Cast | exp.TryCast):
        return _fast_lane_value_ok(value.this)
    if isinstance(value, _TIME_NODES):
        return True
    if isinstance(value, exp.Anonymous) and not value.expressions:
        return value.name.lower() in PINNED_TIME_FUNCTIONS
    if isinstance(value, exp.Dot) and isinstance(value.expression, exp.Anonymous):
        return _fast_lane_value_ok(value.expression) and _qualifier(value.expression) == [
            "pg_catalog"
        ]
    return False


def _is_fast_lane(statement: exp.Expression) -> bool:
    if not isinstance(statement, exp.Insert):
        return False
    values = statement.expression
    if not isinstance(values, exp.Values) or not 1 <= len(values.expressions) <= FAST_LANE_MAX_ROWS:
        return False
    if statement.args.get("with_") or statement.args.get("conflict"):
        return False
    if statement.args.get("returning"):
        return False
    if statement.find(exp.Subquery, exp.Select):
        return False
    return all(
        isinstance(row, exp.Tuple) and all(_fast_lane_value_ok(v) for v in row.expressions)
        for row in values.expressions
    )


def _root_of(statement: exp.Expression) -> exp.Expression:
    node = statement
    while isinstance(node, exp.Subquery | exp.Paren) and node.this is not None:
        node = node.this
    return node


def _analyze_statement(statement: exp.Expression, acc: _Acc) -> bool:
    """Fold one parsed statement into `acc`. Returns True if it is a fast-lane INSERT."""
    root = _root_of(statement)
    root_name = _type_name(root)
    is_read = isinstance(root, exp.Select | exp.SetOperation)

    if root_name is None or any(root.find_all(*_UNSUPPORTED)):
        acc.raise_kind(Kind.UNKNOWN, "unsupported_statement")
        return False

    dml_nodes = list(root.find_all(*_DML))
    ddl_nodes = list(root.find_all(*_DDL))
    for node in (*dml_nodes, *ddl_nodes):
        name = _type_name(node)
        if name:
            acc.types.add(name)
    acc.types.add(root_name)
    if acc.statement_type is None:
        acc.statement_type = _type_name(dml_nodes[0]) if is_read and dml_nodes else root_name

    if is_read:
        if dml_nodes:
            acc.raise_kind(Kind.WRITE)
        if root.find(exp.Into):
            acc.raise_kind(Kind.DDL)
    elif isinstance(root, _DML):
        acc.raise_kind(Kind.WRITE)
    else:
        acc.raise_kind(Kind.DDL)

    for table in root.find_all(exp.Table):
        label = _table_label(table)
        if label is None or _is_cte_reference(table):
            continue
        acc.tables.add(label)
        acc.schemas.add(_table_schema(table))
    for node in dml_nodes:
        for table in _target_tables(node):
            label = _table_label(table)
            if label is not None and not _is_cte_reference(table):
                acc.targets.add(label)

    volatile = _scan_functions(root, acc)
    if acc.side_effects or acc.unknown_functions:
        acc.raise_kind(Kind.WRITE)

    for node in root.find_all(exp.Delete, exp.Update):
        if isinstance(node.parent, exp.When):
            continue  # a MERGE action: its rows are chosen by the MERGE's own ON condition
        where = node.args.get("where")
        if where is None or _where_is_constant_true(where):
            acc.has_where = False
    if _flag_volatile(volatile):
        acc.volatile_in_where = True
    if any(
        _has_unordered_limit(node) for node in dml_nodes if not isinstance(node, exp.TruncateTable)
    ):
        acc.unordered_limit = True

    return _is_fast_lane(root)


# --- Pinning -----------------------------------------------------------------------------------

_FUNCTION_FORM_TYPES = {
    "now": "timestamptz",
    "statement_timestamp": "timestamptz",
    "clock_timestamp": "timestamptz",
    "transaction_timestamp": "timestamptz",
}
_KEYWORD_FORM_TYPES = {
    "current_timestamp": "timestamptz",
    "current_date": "date",
    "current_time": "timetz",
    "localtimestamp": "timestamp",
    "localtime": "time",
}


def _pinned_literal(sql_type: str, now: datetime) -> str:
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    if now.microsecond:
        stamp += f".{now.microsecond:06d}"
    return {
        "timestamptz": stamp + "Z",
        "timestamp": stamp,
        "date": now.strftime("%Y-%m-%d"),
        "time": stamp[11:],
        "timetz": stamp[11:] + "+00",
    }[sql_type]


def _pin(sql: str, tokens: list[Token], now: datetime) -> tuple[str, dict[str, str]]:
    """Replace each clock function with `('<UTC instant>'::<type>)`, by character span."""
    edits: list[tuple[int, int, str]] = []
    values: dict[str, str] = {}
    for i, token in enumerate(tokens):
        if token.token_type in _STRING_TOKEN_TYPES:
            continue
        word = token.text.lower()
        start = token.start
        if i and tokens[i - 1].token_type == TokenType.DOT:
            # Only `pg_catalog.now()` is the real clock; any other prefix is left for the check.
            if (
                word in _FUNCTION_FORM_TYPES
                and i >= 2
                and tokens[i - 2].token_type in _NAME_TOKEN_TYPES
                and tokens[i - 2].text.lower() == "pg_catalog"
            ):
                start = tokens[i - 2].start
            else:
                continue
        following = [t.token_type for t in tokens[i + 1 : i + 4]]
        if word in _FUNCTION_FORM_TYPES and following[:2] == [TokenType.L_PAREN, TokenType.R_PAREN]:
            sql_type, precision, end = _FUNCTION_FORM_TYPES[word], None, tokens[i + 2].end
            key = f"{word}()"
        elif word in _KEYWORD_FORM_TYPES and token.token_type != TokenType.IDENTIFIER:
            sql_type, precision, end = _KEYWORD_FORM_TYPES[word], None, token.end
            key = word
            if sql_type != "date" and following == [
                TokenType.L_PAREN,
                TokenType.NUMBER,
                TokenType.R_PAREN,
            ]:
                precision = tokens[i + 2].text
                end = tokens[i + 3].end
                key = f"{word}({precision})"
        else:
            continue
        literal = _pinned_literal(sql_type, now)
        cast_type = f"{sql_type}({precision})" if precision else sql_type
        edits.append((start, end + 1, f"('{literal}'::{cast_type})"))
        values[key] = literal
    if not edits:
        return sql, {}
    parts: list[str] = []
    position = 0
    for start, end, replacement in edits:
        parts.append(sql[position:start])
        parts.append(replacement)
        position = end
    parts.append(sql[position:])
    return "".join(parts), values


# --- Display -----------------------------------------------------------------------------------

_COMPARISON_TOKENS = frozenset({TokenType.EQ, TokenType.NEQ, TokenType.LIKE, TokenType.ILIKE})


def _is_sensitive_name(token: Token) -> bool:
    return (
        token.token_type in _NAME_TOKEN_TYPES and SENSITIVE_NAME_RE.search(token.text) is not None
    )


def _is_string(token: Token) -> bool:
    return token.token_type in _STRING_TOKEN_TYPES


def _tied_literals(tokens: list[Token]) -> set[int]:
    """Indexes of string tokens tied to a sensitive-named column (compared, set or inserted)."""
    tied: set[int] = set()
    count = len(tokens)

    def type_at(i: int) -> TokenType | None:
        return tokens[i].token_type if 0 <= i < count else None

    for i, token in enumerate(tokens):
        if _is_sensitive_name(token):
            j = i + 1
            if type_at(j) == TokenType.DCOLON:
                j += 2
            if type_at(j) == TokenType.NOT:
                j += 1
            if type_at(j) in _COMPARISON_TOKENS:
                if j + 1 < count and _is_string(tokens[j + 1]):
                    tied.add(j + 1)
            elif type_at(j) == TokenType.IN and type_at(j + 1) == TokenType.L_PAREN:
                k = j + 2
                while k < count and tokens[k].token_type != TokenType.R_PAREN:
                    if _is_string(tokens[k]):
                        tied.add(k)
                    k += 1
        elif _is_string(token) and type_at(i + 1) in _COMPARISON_TOKENS and i + 2 < count:
            if _is_sensitive_name(tokens[i + 2]):
                tied.add(i)
        elif token.token_type == TokenType.INSERT:
            tied |= _tied_insert_values(tokens, i)
    return tied


def _tied_insert_values(tokens: list[Token], insert_at: int) -> set[int]:
    """String values in `INSERT INTO t (cols) VALUES (...)` that sit in a sensitive column."""
    count = len(tokens)
    i = insert_at + 1
    while i < count and tokens[i].token_type not in (TokenType.L_PAREN, TokenType.VALUES):
        if tokens[i].token_type in (TokenType.SELECT, TokenType.SEMICOLON):
            return set()
        i += 1
    if i >= count or tokens[i].token_type != TokenType.L_PAREN:
        return set()
    columns: list[Token] = []
    i += 1
    while i < count and tokens[i].token_type != TokenType.R_PAREN:
        if tokens[i].token_type != TokenType.COMMA:
            columns.append(tokens[i])
        i += 1
    i += 1
    if i >= count or tokens[i].token_type != TokenType.VALUES:
        return set()
    tied: set[int] = set()
    depth = 0
    position = 0
    for k in range(i + 1, count):
        kind = tokens[k].token_type
        if kind == TokenType.L_PAREN:
            depth += 1
            position = 0 if depth == 1 else position
        elif kind == TokenType.R_PAREN:
            depth -= 1
        elif kind == TokenType.COMMA and depth == 1:
            position += 1
        elif depth == 1 and _is_string(tokens[k]):
            if position < len(columns) and _is_sensitive_name(columns[position]):
                tied.add(k)
        elif depth == 0 and kind != TokenType.COMMA:
            break
    return tied


def _display(sql: str) -> str:
    """`sql` with personal data masked: email and phone literals, sensitive columns, comments."""
    try:
        tokens = _TOKENIZER.tokenize(sql)
    except (SqlglotError, RecursionError):
        return mask_text(sql)
    tied = _tied_literals(tokens)
    parts: list[str] = []
    position = 0
    for i, token in enumerate(tokens):
        parts.append(mask_text(sql[position : token.start]))
        original = sql[token.start : token.end + 1]
        placeholder = None
        if _is_string(token):
            placeholder = MASKED_PLACEHOLDER if i in tied else pii_placeholder(token.text)
        parts.append(f"'{placeholder}'" if placeholder else original)
        position = token.end + 1
    parts.append(mask_text(sql[position:]))
    return "".join(parts)


# --- Entry points ------------------------------------------------------------------------------


def _unknown(sql: str, reason: str) -> Classification:
    try:
        # Input past the cap is not scanned whole: the display is for people, and the scan is
        # not free.
        display = _display(sql[:MAX_SQL_BYTES])
    except Exception:
        display = mask_text(sql[:MAX_SQL_BYTES])
    return Classification(kind=Kind.UNKNOWN, reason=reason, pinned_sql=sql, sql_display=display)


def _analyze(sql: str) -> tuple[Classification, _Acc]:
    """Everything except pinning and display."""
    if len(sql.encode()) > MAX_SQL_BYTES:
        return _unknown(sql, "too_long"), _Acc()
    try:
        tokens = _TOKENIZER.tokenize(sql)
        parsed = sqlglot.parse(sql, read="postgres")
    except (SqlglotError, RecursionError):
        return _unknown(sql, "unparseable"), _Acc()
    groups = _split_statements(tokens)
    statements = [s for s in parsed if s is not None]
    if not groups:
        return _unknown(sql, "empty"), _Acc()
    if len(groups) != len(statements):
        return _unknown(sql, "unparseable"), _Acc()

    acc = _Acc()
    fast_lane = False
    first_transaction_type: str | None = None
    for group, statement in zip(groups, statements, strict=True):
        transaction_type = _transaction_type(group)
        if transaction_type is not None:
            acc.transaction_control = True
            acc.types.add(transaction_type)
            acc.raise_kind(Kind.WRITE)
            first_transaction_type = first_transaction_type or transaction_type
            continue
        # The first statement that is not transaction control gives the statement type.
        fast_lane = _analyze_statement(statement, acc)
    multi = len(groups) > 1
    result = Classification(
        kind=acc.kind,
        reason=acc.reason,
        statement_type=acc.statement_type or first_transaction_type,
        statement_types=tuple(sorted(acc.types)),
        tables=tuple(sorted(acc.tables)),
        target_tables=tuple(sorted(acc.targets)),
        schemas=tuple(sorted(acc.schemas)),
        has_where=acc.has_where,
        multi_statement=multi,
        transaction_control=acc.transaction_control,
        non_deterministic=acc.non_deterministic,
        volatile_in_where=acc.volatile_in_where,
        unordered_limit=acc.unordered_limit,
        fast_lane_eligible=fast_lane and not multi and acc.kind == Kind.WRITE,
        side_effect_functions=tuple(sorted(acc.side_effects)),
        unknown_functions=tuple(sorted(acc.unknown_functions)),
        pinned_sql=sql,
    )
    return result, acc


def _classify(sql: str, now: datetime) -> Classification:
    base, acc = _analyze(sql)
    if base.kind in (Kind.UNKNOWN, Kind.DDL):
        # Not run, so nothing to pin (and a DEFAULT now() in DDL must stay a default).
        return dataclasses.replace(base, sql_display=_display(sql))
    pinned_sql, values = _pin(sql, _TOKENIZER.tokenize(sql), now)
    if pinned_sql != sql:
        again, again_acc = _analyze(pinned_sql)
        if again_acc.time_functions_left or any(
            getattr(again, name) != getattr(base, name) for name in _PIN_CHECKED_FIELDS
        ):
            return _unknown(sql, "pin_check_failed")
    elif acc.time_functions_left:
        # A clock function the pinning did not recognise, such as `other_schema.now()`.
        return _unknown(sql, "pin_check_failed")
    return dataclasses.replace(
        base, pinned_sql=pinned_sql, pinned_values=values, sql_display=_display(pinned_sql)
    )


def classify(sql: str, *, now: datetime) -> Classification:
    """Classify one agent SQL input. Pure and synchronous; never raises for bad SQL.

    `now` is the intercept time (aware); every clock function in the SQL is pinned to it.
    """
    now = require_aware_utc(now)
    try:
        return _classify(sql, now)
    except Exception:
        return _unknown(sql, "check_error")


async def classify_in_worker(sql: str, clock: Clock) -> tuple[Classification, int]:
    """Run `classify` on the classifier thread pool with a 2 s cap. Returns (result, milliseconds).

    A timeout or a crash gives `unknown`, never an exception, so the write is blocked.
    """
    now = clock.now()
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, partial(classify, sql, now=now)),
            CLASSIFY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        result = _unknown(sql, "parse_timeout")
    except Exception as exc:
        # The class only: an exception message could hold the SQL.
        logger.error("classify_failed", error_class=type(exc).__name__)
        result = _unknown(sql, "check_error")
    return result, round((time.perf_counter() - started) * 1000)
