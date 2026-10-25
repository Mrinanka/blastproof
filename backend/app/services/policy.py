"""Policy rules (backend plan step 9): turn the classifier's facts, and later the shadow run's diff,
into allow / review / block with a reason.

Pure code: nothing here reads a database, a clock or the network after `load_policy`. It fails
closed: a bad rules file stops startup, and malformed facts raise `PolicyFactsError` for the caller
to block on. Two passes:

- Pass 1 runs on the classification alone. A rule that needs the diff is skipped (neither true nor
  false). A matched block ends the flow; a matched fast-lane allow skips the preview.
- Pass 2 runs after the shadow run with every key. The strictest outcome wins, and an allow needs a
  rule that positively matched: no match at all means review.

Rule order never changes an outcome, only which matching rule's reason is shown.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic import Field as PydanticField
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.db.enums import Decision
from app.services.classifier import STATEMENT_TYPES, Classification, Kind
from app.services.masking import SENSITIVE_NAME_RE

Outcome = Decision

SQL_KEYS: Final = frozenset(
    {
        "statement_in",
        "kind_in",
        "tables_any",
        "schemas_any",
        "has_where",
        "multi_statement",
        "transaction_control",
        "volatile_in_where",
        "unordered_limit",
        "fast_lane",
        "side_effect_function",
    }
)
DIFF_KEYS: Final = frozenset(
    {
        "rows_affected_gt",
        "rows_affected_lte",
        "fraction_of_table_gt",
        "fraction_of_table_gte",
        "cascade_tables_any",
        "derived_group_gt",
    }
)

NO_RULE_REASON: Final = "No rule allows this write automatically, so it needs a person's review."
PREVIEW_REASON: Final = "No rule decided this from the SQL alone, so it is previewed first."

_STRICTNESS = {Outcome.ALLOW: 0, Outcome.REVIEW: 1, Outcome.BLOCK: 2}
_IDENTIFIER = r"[a-z_][a-z0-9_]*"
_TABLE_LABEL_RE = re.compile(rf"^{_IDENTIFIER}(\.{_IDENTIFIER})?$")
_IDENTIFIER_RE = re.compile(f"^{_IDENTIFIER}$")
_QUALIFIED_RE = re.compile(rf"^{_IDENTIFIER}\.{_IDENTIFIER}$")

# What a derived-group condition may contain: bare columns, literals, comparisons, IN, IS [NOT]
# NULL, AND / OR / NOT and parentheses. No functions, subqueries or casts.
_CONDITION_NODES = (
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Null,
    exp.Boolean,
    exp.Neg,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.In,
    exp.Is,
    exp.Not,
    exp.And,
    exp.Or,
    exp.Paren,
)
_BOOLEAN_ROOTS = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.In,
    exp.Is,
    exp.Not,
    exp.And,
    exp.Or,
    exp.Paren,
    exp.Boolean,
)


class PolicyFileError(ValueError):
    """The rules file is unusable. Raised at startup; the message names the rule and the key."""


class PolicyFactsError(ValueError):
    """The diff facts are malformed or incomplete. The caller must block the write."""


class Route(StrEnum):
    """What pass 1 tells the workflow to do next."""

    BLOCK = "block"
    FAST_LANE = "fast_lane"
    PREVIEW = "preview"


# --- The rules file ----------------------------------------------------------------------------


def _parse_condition(text: str) -> frozenset[str]:
    """Check a derived-group condition and return the columns it names."""
    if ";" in text:
        raise ValueError("a derived-group condition must be one expression without ';'")
    try:
        statements = sqlglot.parse(text, read="postgres")
    except SqlglotError as exc:
        raise ValueError(f"the condition does not parse: {str(exc).splitlines()[0]}") from None
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("a derived-group condition must be one expression")
    root = statements[0]
    if not isinstance(root, _BOOLEAN_ROOTS):
        raise ValueError("a derived-group condition must be a true-or-false expression")
    columns: set[str] = set()
    for node in root.walk():
        if not isinstance(node, _CONDITION_NODES):
            raise ValueError(
                f"a derived-group condition may not contain {type(node).__name__}; use only"
                " columns, literals, comparisons, IN, IS NULL, AND, OR, NOT and parentheses"
            )
        if isinstance(node, exp.Column):
            if node.args.get("table") or node.args.get("db"):
                raise ValueError("write columns without a table name in a derived-group condition")
            name = node.name
            if not _IDENTIFIER_RE.match(name):
                raise ValueError(f"column {name!r} must be a lower-case identifier")
            columns.add(name)
    if not columns:
        raise ValueError("a derived-group condition must name at least one column")
    return frozenset(columns)


class DerivedGroup(BaseModel):
    """A named SQL condition counted over a table's affected rows, e.g. `plan in ('pro')`."""

    model_config = ConfigDict(frozen=True)

    condition: str
    columns: frozenset[str]

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("must be a SQL condition written as text")
        return {"condition": value, "columns": _parse_condition(value)}


def _lower_names(values: list[str], pattern: re.Pattern[str], what: str) -> list[str]:
    for value in values:
        if not pattern.match(value):
            raise ValueError(f"{value!r} is not a valid {what} (lower-case letters, digits, _)")
    return values


class Conditions(BaseModel):
    """The `when` of a rule. Every key is optional, at least one is required, none may repeat."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    statement_in: list[str] | None = None
    kind_in: list[str] | None = None
    tables_any: list[str] | None = None
    schemas_any: list[str] | None = None
    has_where: bool | None = None
    multi_statement: bool | None = None
    transaction_control: bool | None = None
    volatile_in_where: bool | None = None
    unordered_limit: bool | None = None
    fast_lane: bool | None = None
    side_effect_function: bool | None = None
    rows_affected_gt: int | None = PydanticField(default=None, ge=0)
    rows_affected_lte: int | None = PydanticField(default=None, ge=0)
    fraction_of_table_gt: float | None = PydanticField(default=None, ge=0, le=1)
    fraction_of_table_gte: float | None = PydanticField(default=None, ge=0, le=1)
    cascade_tables_any: list[str] | None = None
    derived_group_gt: dict[str, int] | None = None

    @model_validator(mode="after")
    def _check(self) -> "Conditions":
        given = {key: getattr(self, key) for key in self.model_fields_set}
        if not given:
            raise ValueError("when: needs at least one condition")
        for key, value in given.items():
            if value is None:
                raise ValueError(f"{key}: must have a value")
            if isinstance(value, list) and not value:
                raise ValueError(f"{key}: must not be an empty list")
        if self.statement_in is not None:
            unknown = sorted(set(self.statement_in) - STATEMENT_TYPES)
            if unknown:
                raise ValueError(
                    f"statement_in: unknown statement type {unknown}; use {sorted(STATEMENT_TYPES)}"
                )
        if self.kind_in is not None:
            unknown = sorted(set(self.kind_in) - {k.value for k in Kind})
            if unknown:
                raise ValueError(f"kind_in: unknown kind {unknown}; use {[k.value for k in Kind]}")
        if self.tables_any is not None:
            _lower_names(self.tables_any, _TABLE_LABEL_RE, "table name in tables_any")
        if self.schemas_any is not None:
            _lower_names(self.schemas_any, _IDENTIFIER_RE, "schema name in schemas_any")
        if self.cascade_tables_any is not None:
            _lower_names(self.cascade_tables_any, _TABLE_LABEL_RE, "table in cascade_tables_any")
        if self.derived_group_gt is not None:
            for name, count in self.derived_group_gt.items():
                if not _QUALIFIED_RE.match(name):
                    raise ValueError(f"derived_group_gt: {name!r} must look like table.group")
                if count < 0:
                    raise ValueError(f"derived_group_gt: {name} must be 0 or more")
        return self

    @property
    def uses_diff(self) -> bool:
        return any(getattr(self, key) is not None for key in DIFF_KEYS)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = PydanticField(min_length=1)
    when: Conditions
    then: Outcome
    reason: str = PydanticField(min_length=1)

    @model_validator(mode="after")
    def _check_fast_lane(self) -> "Rule":
        if self.when.fast_lane is True:
            if self.then != Outcome.ALLOW:
                raise ValueError("fast_lane: true is only valid on a rule that allows")
            if not self.when.tables_any:
                raise ValueError("fast_lane: true needs tables_any (the tables it opens)")
            if self.when.uses_diff:
                raise ValueError(
                    "fast_lane: true cannot be combined with a diff key; "
                    "pass 1 would skip the rule forever"
                )
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rules: list[Rule]
    important_columns: dict[str, list[str]] = {}
    derived_groups: dict[str, dict[str, DerivedGroup]] = {}
    sensitive_columns: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        seen: set[str] = set()
        for rule in self.rules:
            if rule.name in seen:
                raise ValueError(f"rules: the name {rule.name!r} is used twice")
            seen.add(rule.name)
            for key in rule.when.derived_group_gt or {}:
                table, group = key.split(".")
                if group not in self.derived_groups.get(table, {}):
                    raise ValueError(
                        f"rule {rule.name!r}, derived_group_gt: {key!r} is not defined "
                        "under derived_groups"
                    )
        for section in (self.important_columns, self.sensitive_columns):
            for table, columns in section.items():
                _lower_names([table], _IDENTIFIER_RE, "table name")
                _lower_names(columns, _IDENTIFIER_RE, "column name")
        for table, groups in self.derived_groups.items():
            _lower_names([table, *groups], _IDENTIFIER_RE, "table or group name")
        return self

    def is_sensitive(self, table: str, column: str) -> bool:
        """True for a configured sensitive column or one whose name looks like personal data."""
        column = column.lower()
        return column in self.sensitive_columns.get(table.lower(), []) or bool(
            SENSITIVE_NAME_RE.search(column)
        )

    def named_columns(self) -> dict[str, set[str]]:
        """Every column the file names, per table."""
        named: dict[str, set[str]] = {}
        for section in (self.important_columns, self.sensitive_columns):
            for table, columns in section.items():
                named.setdefault(table, set()).update(columns)
        for table, groups in self.derived_groups.items():
            for group in groups.values():
                named.setdefault(table, set()).update(group.columns)
        return named


class _UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML keeps the last of two equal keys without a word; a policy file must not."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)  # type: ignore[no-untyped-call]
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                raise PolicyFileError(
                    f"line {key_node.start_mark.line + 1}: a key must be plain text"
                ) from None
            if duplicate:
                raise PolicyFileError(
                    f"duplicate key {key!r} on line {key_node.start_mark.line + 1}; "
                    "the later value would silently replace the earlier one"
                )
        return super().construct_mapping(node, deep)


def _describe(raw: Mapping[str, Any], error: ValidationError) -> str:
    rules = raw.get("rules")
    messages: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        loc = list(item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        prefix = ""
        if len(loc) >= 2 and loc[0] == "rules" and isinstance(loc[1], int):
            name = None
            if isinstance(rules, list) and loc[1] < len(rules) and isinstance(rules[loc[1]], dict):
                name = rules[loc[1]].get("name")
            prefix = f"rule {name!r}" if isinstance(name, str) and name else f"rule #{loc[1] + 1}"
            loc = loc[2:]
        path = ".".join(str(part) for part in loc)
        messages.append(": ".join(part for part in (prefix, path, message) if part))
    return "; ".join(messages)


def load_policy(path: Path) -> Policy:
    """Read and validate the rules file. Raises `PolicyFileError` on any problem."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyFileError(f"cannot read the policy file {path}: {type(exc).__name__}") from None
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 (SafeLoader subclass)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise PolicyFileError(f"{path} is not valid YAML{where}") from None
    if not isinstance(raw, dict):
        raise PolicyFileError(f"{path} must be a mapping with a 'rules' list")
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyFileError(f"invalid policy file {path}: {_describe(raw, exc)}") from None


def check_columns(policy: Policy, columns_by_table: Mapping[str, set[str]]) -> None:
    """Every table and column the file names must exist in the company database.

    `columns_by_table` is read from `information_schema` by the caller (the shadow run, step 10).
    """
    problems: list[str] = []
    for table, columns in sorted(policy.named_columns().items()):
        if table not in columns_by_table:
            problems.append(f"table {table!r} does not exist")
            continue
        for column in sorted(columns - columns_by_table[table]):
            problems.append(f"column {table}.{column} does not exist")
    if problems:
        raise PolicyFileError("policy file does not match the database: " + "; ".join(problems))


# --- Facts -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TableFacts:
    """What the shadow run measured for one table. `rows_before` is the table's total row count."""

    deleted: int = 0
    updated: int = 0
    inserted: int = 0
    rows_before: int | None = None
    is_cascade: bool = False

    @property
    def changed(self) -> int:
        return self.deleted + self.updated + self.inserted


@dataclass(frozen=True)
class DiffFacts:
    tables: Mapping[str, TableFacts] = field(default_factory=dict)
    # "users.paying" -> how many of the affected users rows are in that group.
    derived_groups: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleMatch:
    name: str
    outcome: Outcome
    reason: str


@dataclass(frozen=True)
class PassOneResult:
    route: Route
    matched: tuple[RuleMatch, ...]
    # Rules that need the diff and so were not judged in this pass.
    skipped: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class PolicyDecision:
    decision: Outcome
    matched: tuple[RuleMatch, ...]
    reason: str


def _check_facts(facts: DiffFacts) -> None:
    for table, t in facts.tables.items():
        counts = (t.deleted, t.updated, t.inserted)
        if any(n < 0 for n in counts) or (t.rows_before is not None and t.rows_before < 0):
            raise PolicyFactsError(f"{table}: row counts cannot be negative")
        if t.changed and t.rows_before is None:
            raise PolicyFactsError(f"{table}: changed but its total row count is missing")
        if t.deleted + t.updated > 0 and t.rows_before == 0:
            raise PolicyFactsError(f"{table}: rows changed in a table measured as empty")
    for group, count in facts.derived_groups.items():
        if count < 0:
            raise PolicyFactsError(f"{group}: a group count cannot be negative")


# --- Evaluation --------------------------------------------------------------------------------


def _sql_matches(when: Conditions, c: Classification) -> bool:
    checks: list[tuple[object, bool]] = [
        (when.statement_in, any(t in (when.statement_in or ()) for t in c.statement_types)),
        (when.kind_in, c.kind.value in (when.kind_in or ())),
        (when.tables_any, any(t in (when.tables_any or ()) for t in c.tables)),
        (when.schemas_any, any(s in (when.schemas_any or ()) for s in c.schemas)),
        (when.has_where, c.has_where == when.has_where),
        (when.multi_statement, c.multi_statement == when.multi_statement),
        (when.transaction_control, c.transaction_control == when.transaction_control),
        (when.volatile_in_where, c.volatile_in_where == when.volatile_in_where),
        (when.unordered_limit, c.unordered_limit == when.unordered_limit),
        (when.fast_lane, c.fast_lane_eligible == when.fast_lane),
        (when.side_effect_function, bool(c.side_effect_functions) == when.side_effect_function),
    ]
    return all(ok for given, ok in checks if given is not None)


def _max_fraction(facts: DiffFacts) -> Fraction | None:
    fractions = [
        Fraction(t.deleted + t.updated, t.rows_before)
        for t in facts.tables.values()
        if t.rows_before and t.deleted + t.updated > 0
    ]
    return max(fractions, default=None)


def _group_count(key: str, facts: DiffFacts) -> int:
    table = key.split(".")[0]
    touched = facts.tables.get(table)
    if touched is None or touched.changed == 0:
        return 0
    if key not in facts.derived_groups:
        raise PolicyFactsError(f"{key}: {table} changed but the group count is missing")
    return facts.derived_groups[key]


def _diff_matches(when: Conditions, facts: DiffFacts) -> bool:
    if not when.uses_diff:
        return True
    total = sum(t.changed for t in facts.tables.values())
    fraction = _max_fraction(facts) or Fraction(0)
    if when.rows_affected_gt is not None and not total > when.rows_affected_gt:
        return False
    if when.rows_affected_lte is not None and not total <= when.rows_affected_lte:
        return False
    if when.fraction_of_table_gt is not None and not (
        fraction > Fraction(str(when.fraction_of_table_gt))
    ):
        return False
    if when.fraction_of_table_gte is not None and not (
        fraction >= Fraction(str(when.fraction_of_table_gte))
    ):
        return False
    if when.cascade_tables_any is not None and not any(
        facts.tables.get(t) is not None and facts.tables[t].is_cascade and facts.tables[t].changed
        for t in when.cascade_tables_any
    ):
        return False
    groups = when.derived_group_gt or {}
    return all(_group_count(key, facts) > minimum for key, minimum in groups.items())


def _match(rule: Rule) -> RuleMatch:
    return RuleMatch(name=rule.name, outcome=rule.then, reason=rule.reason)


def evaluate_pass_one(policy: Policy, c: Classification) -> PassOneResult:
    """Pass 1: only the SQL-only keys are known. Rules that need the diff are skipped."""
    matched: list[RuleMatch] = []
    skipped: list[str] = []
    fast_lane_rule: Rule | None = None
    for rule in policy.rules:
        if rule.when.uses_diff:
            skipped.append(rule.name)
        elif _sql_matches(rule.when, c):
            matched.append(_match(rule))
            if rule.then == Outcome.ALLOW and rule.when.fast_lane is True and not fast_lane_rule:
                fast_lane_rule = rule
    blocks = [m for m in matched if m.outcome == Outcome.BLOCK]
    if blocks:
        return PassOneResult(Route.BLOCK, tuple(matched), tuple(skipped), blocks[0].reason)
    reviews = [m for m in matched if m.outcome == Outcome.REVIEW]
    if fast_lane_rule is not None and c.fast_lane_eligible and not reviews:
        return PassOneResult(Route.FAST_LANE, tuple(matched), tuple(skipped), fast_lane_rule.reason)
    return PassOneResult(Route.PREVIEW, tuple(matched), tuple(skipped), PREVIEW_REASON)


def evaluate_pass_two(policy: Policy, c: Classification, facts: DiffFacts) -> PolicyDecision:
    """Pass 2: every key, after the shadow run. Raises `PolicyFactsError` on bad facts."""
    _check_facts(facts)
    matched = tuple(
        _match(rule)
        for rule in policy.rules
        if _sql_matches(rule.when, c) and _diff_matches(rule.when, facts)
    )
    if not matched:
        return PolicyDecision(Outcome.REVIEW, (), NO_RULE_REASON)
    decision = max((m.outcome for m in matched), key=_STRICTNESS.__getitem__)
    reason = next(m.reason for m in matched if m.outcome == decision)
    return PolicyDecision(decision, matched, reason)


__all__ = [
    "DIFF_KEYS",
    "SQL_KEYS",
    "Conditions",
    "DerivedGroup",
    "DiffFacts",
    "PassOneResult",
    "Policy",
    "PolicyDecision",
    "PolicyFactsError",
    "PolicyFileError",
    "Route",
    "Rule",
    "RuleMatch",
    "TableFacts",
    "check_columns",
    "evaluate_pass_one",
    "evaluate_pass_two",
    "load_policy",
]
