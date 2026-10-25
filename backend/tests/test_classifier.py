"""DW-B08: the classifier (backend plan step 8), table-driven.

Each row asserts a subset of the `Classification` fields. Rows that matter for a pass-1 rule
(step 9) name the field the rule reads.
"""

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.services import classifier
from app.services.classifier import Classification, Kind, classify, classify_in_worker

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def c(sql: str) -> Classification:
    return classify(sql, now=NOW)


def row(sql: str, id: str, **expected: Any) -> Any:
    return pytest.param(sql, expected, id=id)


CASES = [
    # --- reads
    row("SELECT 1", "plain-select", kind=Kind.READ, statement_type="select", tables=()),
    row(
        "SELECT * FROM users u JOIN orders o ON o.user_id = u.id",
        "join",
        kind=Kind.READ,
        tables=("orders", "users"),
    ),
    row(
        "SELECT id FROM users UNION SELECT user_id FROM orders",
        "union",
        kind=Kind.READ,
        statement_type="select",
        tables=("orders", "users"),
    ),
    row(
        "WITH paying AS (SELECT * FROM users WHERE plan <> 'free') SELECT count(*) FROM paying",
        "read-only-cte",
        kind=Kind.READ,
        tables=("users",),  # `paying` is a CTE, not a table
    ),
    row(
        "SELECT to_char(created_at, 'YYYY'), date_trunc('month', created_at), age(created_at),"
        " jsonb_build_object('a', 1), pg_sleep(1), count(*) FROM users GROUP BY 1, 2, 3",
        "allowlisted-functions",
        kind=Kind.READ,
        unknown_functions=(),
        non_deterministic=False,
    ),
    row(
        "SELECT pg_catalog.upper(name) FROM users",
        "qualified-pg-catalog-function",
        kind=Kind.READ,
        schemas=("pg_catalog", "public"),
    ),
    row("SELECT 1 FROM generate_series(1, 3)", "table-function", kind=Kind.READ, tables=()),
    row(
        "WITH users AS (SELECT * FROM users) SELECT * FROM users",
        "cte-named-like-its-source",
        kind=Kind.READ,
        tables=("users",),
    ),
    row("(SELECT 1)", "parenthesised-select", kind=Kind.READ, statement_type="select"),
    row(
        "SELECT 1;;", "trailing-semicolons-are-one-statement", kind=Kind.READ, multi_statement=False
    ),
    # --- hidden writes
    row(
        "WITH gone AS (DELETE FROM users WHERE plan = 'free' RETURNING id)"
        " SELECT count(*) FROM gone",
        "cte-delete",
        kind=Kind.WRITE,
        statement_type="delete",
        statement_types=("delete", "select"),
        tables=("users",),
        target_tables=("users",),
        has_where=True,
    ),
    row(
        "WITH gone AS (DELETE FROM users RETURNING id) SELECT count(*) FROM gone",
        "cte-delete-without-where",
        kind=Kind.WRITE,
        has_where=False,
    ),
    row(
        "WITH x AS (UPDATE users SET plan = 'free' WHERE id = 1 RETURNING id) SELECT * FROM x",
        "cte-update",
        kind=Kind.WRITE,
        statement_type="update",
        statement_types=("select", "update"),
    ),
    row(
        "UPDATE users SET plan = 'pro' FROM organizations o"
        " WHERE users.organization_id = o.id AND o.id = 7",
        "update-from",
        kind=Kind.WRITE,
        statement_type="update",
        tables=("organizations", "users"),
        target_tables=("users",),
        has_where=True,
    ),
    row(
        "DELETE FROM orders USING users WHERE orders.user_id = users.id AND users.plan = 'free'",
        "delete-using",
        kind=Kind.WRITE,
        tables=("orders", "users"),
        target_tables=("orders",),
    ),
    row(
        "MERGE INTO users u USING orders o ON u.id = o.user_id WHEN MATCHED THEN DELETE",
        "merge",
        kind=Kind.WRITE,
        statement_type="merge",
        target_tables=("users",),
    ),
    # --- WHERE checks
    row("DELETE FROM users", "delete-no-where", kind=Kind.WRITE, has_where=False),
    row("UPDATE users SET plan = 'free'", "update-no-where", kind=Kind.WRITE, has_where=False),
    row("DELETE FROM orders WHERE true", "where-true", has_where=False),
    row("DELETE FROM orders WHERE 1 = 1", "where-one-equals-one", has_where=False),
    row("DELETE FROM orders WHERE 1 = 1 OR id = 5", "where-true-or-x", has_where=False),
    row("DELETE FROM orders WHERE NOT false", "where-not-false", has_where=False),
    row("DELETE FROM orders WHERE id > 0", "where-id-gt-zero", has_where=True),
    row("DELETE FROM orders WHERE (id = 1)", "where-parenthesised", has_where=True),
    row(
        "INSERT INTO t (a) SELECT a FROM u",
        "insert-needs-no-where",
        kind=Kind.WRITE,
        has_where=True,
    ),
    row(
        "DELETE FROM orders WHERE " + " OR ".join(f"id = {i}" for i in range(1000)),
        "long-or-chain-is-not-simplified",
        kind=Kind.WRITE,
        has_where=True,
    ),
    row(
        "DELETE FROM orders WHERE " + " OR ".join(f"id = {i}" for i in range(1000)) + " OR 1 = 1",
        "long-or-chain-with-a-constant-true-term",
        kind=Kind.WRITE,
        has_where=False,
    ),
    # --- fast lane
    row(
        "INSERT INTO customer_notes (user_id, body) VALUES (42, 'Called about renewal')",
        "fast-one-row",
        kind=Kind.WRITE,
        statement_type="insert",
        target_tables=("customer_notes",),
        fast_lane_eligible=True,
    ),
    row(
        "INSERT INTO customer_notes (user_id, body) VALUES "
        + ", ".join(f"({i}, 'n')" for i in range(10)),
        "fast-ten-rows",
        fast_lane_eligible=True,
    ),
    row(
        "INSERT INTO t (a, b, c, d, e) VALUES (now(), NULL, DEFAULT, -5, '2026-01-01'::date)",
        "fast-allowed-value-kinds",
        fast_lane_eligible=True,
        non_deterministic=True,
    ),
    row(
        "INSERT INTO t (a) VALUES (TRUE), (CAST('7' AS int))",
        "fast-boolean-and-cast",
        fast_lane_eligible=True,
    ),
    row(
        "INSERT INTO customer_notes (user_id, body) VALUES "
        + ", ".join(f"({i}, 'n')" for i in range(11)),
        "slow-eleven-rows",
        kind=Kind.WRITE,
        fast_lane_eligible=False,
    ),
    row(
        "INSERT INTO t (a) VALUES (upper('x'))", "slow-function-in-values", fast_lane_eligible=False
    ),
    row(
        "INSERT INTO t (a) VALUES (gen_random_uuid())",
        "slow-random-uuid-in-values",
        fast_lane_eligible=False,
        non_deterministic=True,
        volatile_in_where=False,
    ),
    row(
        "INSERT INTO t (a) VALUES (1) ON CONFLICT DO NOTHING",
        "slow-on-conflict",
        fast_lane_eligible=False,
    ),
    row("INSERT INTO t (a) VALUES (1) RETURNING id", "slow-returning", fast_lane_eligible=False),
    row("INSERT INTO t (a) SELECT a FROM u", "slow-insert-select", fast_lane_eligible=False),
    row(
        "WITH x AS (SELECT 1) INSERT INTO t (a) VALUES (1)",
        "slow-with-insert",
        fast_lane_eligible=False,
    ),
    row("INSERT INTO t (a) VALUES ((SELECT 1))", "slow-subquery-value", fast_lane_eligible=False),
    row("INSERT INTO t DEFAULT VALUES", "slow-default-values", fast_lane_eligible=False),
    row("INSERT INTO t (a) VALUES ('now')", "slow-special-string-value", fast_lane_eligible=False),
    row(
        "INSERT INTO t (a) VALUES ('now'::timestamptz)",
        "slow-special-string-cast",
        fast_lane_eligible=False,
        non_deterministic=True,
    ),
    row("UPDATE t SET a = 1 WHERE id = 1", "slow-update", fast_lane_eligible=False),
    # --- several statements
    row("SELECT 1; SELECT 2", "two-selects", kind=Kind.READ, multi_statement=True),
    row(
        "BEGIN; DELETE FROM users WHERE id = 5; COMMIT;",
        "begin-delete-commit",
        kind=Kind.WRITE,
        multi_statement=True,
        transaction_control=True,
        statement_type="delete",
        fast_lane_eligible=False,
    ),
    row(
        "DELETE FROM users WHERE id = 1; DELETE FROM users",
        "second-statement-has-no-where",
        multi_statement=True,
        has_where=False,
    ),
    # --- transaction control
    row("BEGIN", "begin", kind=Kind.WRITE, transaction_control=True, statement_type="begin"),
    row("START TRANSACTION", "start-transaction", kind=Kind.WRITE, transaction_control=True),
    row("COMMIT", "commit", kind=Kind.WRITE, transaction_control=True),
    row("END", "end", kind=Kind.WRITE, transaction_control=True),
    row("ROLLBACK", "rollback", kind=Kind.WRITE, transaction_control=True),
    row("ABORT", "abort", kind=Kind.WRITE, transaction_control=True),
    row("SAVEPOINT a", "savepoint", kind=Kind.WRITE, transaction_control=True),
    row(
        "ROLLBACK TO SAVEPOINT a",
        "rollback-to-savepoint",
        kind=Kind.WRITE,
        transaction_control=True,
    ),
    row(
        "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE",
        "set-transaction",
        kind=Kind.WRITE,
        transaction_control=True,
    ),
    row(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "set-session-characteristics",
        kind=Kind.WRITE,
        transaction_control=True,
    ),
    row(
        "RELEASE SAVEPOINT a",
        "release-savepoint-is-unparseable",
        kind=Kind.UNKNOWN,
        reason="unparseable",
    ),
    # --- DDL and truncate
    row("DROP TABLE users CASCADE", "drop", kind=Kind.DDL, statement_type="drop"),
    row(
        "TRUNCATE TABLE orders CASCADE",
        "truncate",
        kind=Kind.WRITE,
        statement_type="truncate",
        target_tables=("orders",),
    ),
    row(
        "ALTER TABLE users ADD COLUMN churn integer", "alter", kind=Kind.DDL, statement_type="alter"
    ),
    row("CREATE TABLE t AS SELECT 1", "create-table-as", kind=Kind.DDL, statement_type="create"),
    row("SELECT * INTO users_copy FROM users", "select-into", kind=Kind.DDL),
    row("GRANT SELECT ON users TO bob", "grant", kind=Kind.DDL, statement_type="grant"),
    row("REVOKE SELECT ON users FROM bob", "revoke", kind=Kind.DDL, statement_type="revoke"),
    row("COMMENT ON TABLE users IS 'x'", "comment", kind=Kind.DDL),
    # --- unknown
    row("DO $$ BEGIN PERFORM 1; END $$", "do", kind=Kind.UNKNOWN, reason="unsupported_statement"),
    row("CALL do_something()", "call", kind=Kind.UNKNOWN, reason="unsupported_statement"),
    row("EXPLAIN ANALYZE DELETE FROM users", "explain-analyze", kind=Kind.UNKNOWN),
    row("VACUUM users", "vacuum", kind=Kind.UNKNOWN, reason="unsupported_statement"),
    row("COPY users TO STDOUT", "copy", kind=Kind.UNKNOWN),
    row("SET search_path = blastproof", "set-search-path", kind=Kind.UNKNOWN),
    row("LISTEN channel", "listen", kind=Kind.UNKNOWN),
    row("NOTIFY channel", "notify", kind=Kind.UNKNOWN),
    row("TABLE users", "table-shorthand", kind=Kind.UNKNOWN),
    row(
        "SELECT * FROM users FOR UPDATE",
        "for-update",
        kind=Kind.UNKNOWN,
        reason="unsupported_statement",
    ),
    row("VALUES (1)", "bare-values", kind=Kind.UNKNOWN),
    # --- the blastproof schema
    row(
        "DELETE FROM blastproof.applied_actions",
        "blastproof-schema",
        schemas=("blastproof",),
        tables=("blastproof.applied_actions",),
    ),
    row(
        "DELETE FROM BLASTPROOF.Applied_Actions",
        "blastproof-schema-upper-case",
        schemas=("blastproof",),
        tables=("blastproof.applied_actions",),
    ),
    row(
        'DELETE FROM "blastproof"."applied_actions"',
        "blastproof-schema-quoted",
        schemas=("blastproof",),
    ),
    row(
        "DELETE FROM company_main.blastproof.applied_actions",
        "blastproof-schema-with-database",
        schemas=("blastproof",),
    ),
    row(
        "SELECT * FROM blastproof.seed_info",
        "blastproof-read",
        kind=Kind.READ,
        schemas=("blastproof",),
    ),
    row(
        "SELECT blastproof.f()",
        "blastproof-function",
        kind=Kind.WRITE,
        schemas=("blastproof",),
        unknown_functions=("f",),
    ),
    row(
        "DELETE FROM public.users WHERE id = 1",
        "public-is-normalised",
        tables=("users",),
        target_tables=("users",),
        schemas=("public",),
    ),
    # --- unordered LIMIT
    row(
        "DELETE FROM orders WHERE id IN (SELECT id FROM orders WHERE status = 'x' LIMIT 100)",
        "limit-in-subquery",
        unordered_limit=True,
    ),
    row(
        "DELETE FROM orders WHERE id IN (SELECT id FROM orders WHERE status = 'x'"
        " ORDER BY id LIMIT 100)",
        "ordered-limit-is-fine",
        unordered_limit=False,
    ),
    row(
        "DELETE FROM orders WHERE id IN (SELECT id FROM orders OFFSET 5)",
        "offset-without-order",
        unordered_limit=True,
    ),
    row(
        "DELETE FROM orders WHERE id IN (SELECT id FROM orders FETCH FIRST 5 ROWS ONLY)",
        "fetch-without-order",
        unordered_limit=True,
    ),
    row("SELECT * FROM orders LIMIT 5", "read-with-limit", kind=Kind.READ, unordered_limit=False),
    row(
        "INSERT INTO t (a) SELECT a FROM u LIMIT 5",
        "insert-select-limit",
        unordered_limit=True,
    ),
    row(
        "WITH d AS (DELETE FROM orders WHERE id IN (SELECT id FROM orders LIMIT 3) RETURNING id)"
        " SELECT * FROM d",
        "limit-in-a-cte-delete",
        unordered_limit=True,
    ),
    # --- volatile values
    row(
        "DELETE FROM users WHERE id IN (SELECT id FROM users ORDER BY random() LIMIT 10)",
        "random-in-where-subquery",
        volatile_in_where=True,
        unordered_limit=False,
        non_deterministic=True,
    ),
    row("DELETE FROM users WHERE random() < 0.5", "random-in-where", volatile_in_where=True),
    row(
        "DELETE FROM users WHERE created_at < 'now'::timestamptz",
        "special-string-in-where",
        volatile_in_where=True,
    ),
    row(
        "DELETE FROM users WHERE foo(id) = 1",
        "unknown-function-in-where",
        volatile_in_where=True,
        kind=Kind.WRITE,
    ),
    row(
        "UPDATE users SET plan = 'x' WHERE id = nextval('s')",
        "nextval-in-where",
        volatile_in_where=True,
    ),
    row(
        "DELETE FROM users USING (SELECT random() r) x WHERE users.id = 1",
        "random-in-using",
        volatile_in_where=True,
    ),
    row(
        "INSERT INTO t (a) VALUES (gen_random_uuid())",
        "uuid-in-values-is-allowed",
        volatile_in_where=False,
        non_deterministic=True,
    ),
    row(
        "INSERT INTO t (a) VALUES (nextval('s'))",
        "nextval-in-values-is-allowed",
        volatile_in_where=False,
        non_deterministic=True,
        side_effect_functions=("nextval",),
    ),
    row(
        "UPDATE users SET score = random() WHERE id = 1",
        "random-in-set-is-allowed",
        volatile_in_where=False,
        non_deterministic=True,
    ),
    row(
        "INSERT INTO t (a) SELECT random() FROM u",
        "random-in-insert-select-list-is-allowed",
        volatile_in_where=False,
    ),
    row(
        "INSERT INTO t (a) SELECT a FROM u WHERE random() < 0.5",
        "random-in-insert-select-where",
        volatile_in_where=True,
    ),
    row(
        "WITH d AS (DELETE FROM users WHERE random() < 0.5 RETURNING id) SELECT * FROM d",
        "random-in-cte-delete-where",
        volatile_in_where=True,
    ),
    row(
        "UPDATE users SET plan = 'x' WHERE id = 1 RETURNING random()",
        "random-in-returning-is-allowed",
        volatile_in_where=False,
    ),
    row(
        "SELECT random()",
        "random-in-a-read",
        kind=Kind.READ,
        volatile_in_where=False,
        non_deterministic=True,
    ),
    row(
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = random()"
        " WHEN NOT MATCHED THEN INSERT (a) VALUES (1)",
        "merge-actions-are-not-deletes-without-where",
        kind=Kind.WRITE,
        has_where=True,
        volatile_in_where=False,
    ),
    # --- side effects and unknown functions
    row(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid()",
        "terminate-backends",
        kind=Kind.WRITE,
        side_effect_functions=("pg_terminate_backend",),
        unknown_functions=(),
    ),
    row(
        "SELECT pg_catalog.pg_terminate_backend(1)",
        "qualified-terminate",
        kind=Kind.WRITE,
        side_effect_functions=("pg_terminate_backend",),
    ),
    row(
        "DELETE FROM t WHERE id IN (SELECT pg_terminate_backend(1))",
        "terminate-inside-a-where-subquery",
        kind=Kind.WRITE,
        side_effect_functions=("pg_terminate_backend",),
        volatile_in_where=True,
    ),
    row(
        "SELECT query_to_xml('select 1', true, true, '')",
        "query-to-xml",
        kind=Kind.WRITE,
        side_effect_functions=("query_to_xml",),
    ),
    row(
        "SELECT nextval('s')",
        "nextval-in-a-select",
        kind=Kind.WRITE,
        side_effect_functions=("nextval",),
    ),
    row(
        "SELECT set_config('a', 'b', false)",
        "set-config",
        kind=Kind.WRITE,
        side_effect_functions=("set_config",),
    ),
    row(
        "SELECT pg_advisory_lock(1)",
        "advisory-lock",
        kind=Kind.WRITE,
        side_effect_functions=("pg_advisory_lock",),
    ),
    row(
        "SELECT pg_read_file('/etc/passwd')",
        "read-file",
        kind=Kind.WRITE,
        side_effect_functions=("pg_read_file",),
    ),
    row(
        "SELECT dblink('x', 'select 1')",
        "dblink",
        kind=Kind.WRITE,
        side_effect_functions=("dblink",),
    ),
    row(
        "SELECT pg_create_physical_replication_slot('s')",
        "replication-slot",
        kind=Kind.WRITE,
        side_effect_functions=("pg_create_physical_replication_slot",),
    ),
    row(
        "SELECT my_secret_function(1)",
        "unknown-function-in-a-select",
        kind=Kind.WRITE,
        unknown_functions=("my_secret_function",),
        non_deterministic=True,
    ),
    row(
        "SELECT public.upper('a')",
        "schema-qualified-function-is-not-pg-catalog",
        kind=Kind.WRITE,
        unknown_functions=("upper",),
    ),
    # --- unparseable and size
    row("SELEC 1", "typo", kind=Kind.UNKNOWN, reason="unparseable"),
    row("SELECT (1", "unclosed-paren", kind=Kind.UNKNOWN, reason="unparseable"),
    row("", "empty", kind=Kind.UNKNOWN, reason="empty"),
    row("   \n\t ", "blanks", kind=Kind.UNKNOWN, reason="empty"),
    row("-- nothing here", "comment-only", kind=Kind.UNKNOWN, reason="empty"),
    row(";", "semicolon-only", kind=Kind.UNKNOWN, reason="empty"),
    row(
        "SELECT " + "(" * 3000 + "1" + ")" * 3000,
        "deep-nesting",
        kind=Kind.UNKNOWN,
        reason="unparseable",
    ),
    row("SELECT '" + "x" * 20_500 + "'", "over-20kb", kind=Kind.UNKNOWN, reason="too_long"),
    row(
        "SELECT '" + "é" * 10_500 + "'",
        "over-20kb-counted-in-bytes",
        kind=Kind.UNKNOWN,
        reason="too_long",
    ),
]


@pytest.mark.parametrize(("sql", "expected"), CASES)
def test_classification(sql: str, expected: dict[str, Any]) -> None:
    result = c(sql)
    for name, value in expected.items():
        assert getattr(result, name) == value, f"{name}: {getattr(result, name)!r} != {value!r}"


def test_the_table_has_the_cases_the_done_when_names() -> None:
    assert len(CASES) >= 40
    ids = {case.id for case in CASES}
    assert {
        "cte-delete",
        "update-from",
        "where-true",
        "two-selects",
        "begin-delete-commit",
        "blastproof-schema",
        "limit-in-subquery",
        "slow-function-in-values",
    } <= ids


def test_a_backslash_never_hides_a_second_statement() -> None:
    backslash = chr(92)
    # Standard strings end at the next quote (a backslash is just a character), as in Postgres.
    standard = f"SELECT 'a{backslash}'; DROP TABLE x; --'"
    assert c(standard).kind != Kind.READ
    # An E string treats backslash-quote as an escaped quote, also as in Postgres: one statement.
    escaped = f"SELECT E'a{backslash}'; DROP TABLE x; --'"
    assert (c(escaped).kind, c(escaped).multi_statement) == (Kind.READ, False)


def test_just_under_the_size_cap_is_parsed() -> None:
    sql = "SELECT '" + "x" * 20_000 + "'"
    assert len(sql.encode()) <= classifier.MAX_SQL_BYTES
    assert c(sql).kind == Kind.READ


def test_an_unknown_result_still_carries_the_sql_for_the_audit() -> None:
    result = c("SELEC 1")
    assert result.pinned_sql == "SELEC 1"
    assert result.pinned_values == {}


def test_a_naive_clock_value_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        classify("SELECT 1", now=datetime(2026, 10, 1))


# --- the async wrapper


async def test_worker_returns_the_result_and_a_duration() -> None:
    result, ms = await classify_in_worker("SELECT 1", FrozenClock(NOW))
    assert result.kind == Kind.READ
    assert 0 <= ms < 2000


async def test_worker_gives_unknown_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def blocked(sql: str, *, now: datetime) -> Classification:
        release.wait(10)
        return classify(sql, now=now)

    monkeypatch.setattr(classifier, "classify", blocked)
    started = time.perf_counter()
    try:
        result, ms = await classify_in_worker("SELECT 1", FrozenClock(NOW))
    finally:
        release.set()
    assert (result.kind, result.reason) == (Kind.UNKNOWN, "parse_timeout")
    assert time.perf_counter() - started < 2.5
    assert ms >= 2000


async def test_worker_gives_unknown_on_a_crash_and_logs_only_the_class(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def crash(sql: str, *, now: datetime) -> Classification:
        raise RuntimeError(f"boom while parsing {sql}")

    monkeypatch.setattr(classifier, "classify", crash)
    with caplog.at_level(logging.DEBUG):
        result, _ = await classify_in_worker("DELETE FROM secret_table", FrozenClock(NOW))
    assert (result.kind, result.reason) == (Kind.UNKNOWN, "check_error")
    output = capsys.readouterr().out + capsys.readouterr().err + caplog.text
    assert "secret_table" not in output
    assert "boom" not in output


async def test_worker_reads_the_clock_once_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Spy:
        def now(self) -> datetime:
            events.append("clock")
            return NOW

    real = classifier.classify

    def watched(sql: str, *, now: datetime) -> Classification:
        events.append("classify")
        return real(sql, now=now)

    monkeypatch.setattr(classifier, "classify", watched)
    await classify_in_worker("SELECT now()", Spy())
    assert events == ["clock", "classify"]


async def test_worker_runs_on_the_classifier_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []
    real = classifier.classify

    def watched(sql: str, *, now: datetime) -> Classification:
        names.append(threading.current_thread().name)
        return real(sql, now=now)

    monkeypatch.setattr(classifier, "classify", watched)
    await classify_in_worker("SELECT 1", FrozenClock(NOW))
    assert names[0].startswith("classifier")


async def test_a_slow_parse_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def blocked(sql: str, *, now: datetime) -> Classification:
        release.wait(10)
        return classify(sql, now=now)

    monkeypatch.setattr(classifier, "classify", blocked)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await classify_in_worker("SELECT 1", FrozenClock(NOW))
    finally:
        release.set()
        task.cancel()
    assert ticks > 10


def test_a_huge_where_is_not_simplified_whole() -> None:
    # simplify() on a 1,500-term OR chain takes tens of seconds; the node bound avoids it.
    sql = "DELETE FROM orders WHERE " + " OR ".join(f"id = {i}" for i in range(1500))
    assert len(sql.encode()) < classifier.MAX_SQL_BYTES
    started = time.perf_counter()
    result = c(sql)
    assert time.perf_counter() - started < 3
    assert (result.kind, result.has_where) == (Kind.WRITE, True)
