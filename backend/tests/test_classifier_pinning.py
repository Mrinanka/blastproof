"""DW-B08: pinning clock functions to one UTC instant (ARCH-03, TEST-06)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.services import classifier
from app.services.classifier import Classification, Kind, classify

FROZEN = datetime(2026, 10, 1, tzinfo=UTC)
WITH_MICROSECONDS = datetime(2026, 10, 1, 13, 5, 9, 123456, tzinfo=UTC)

EVERY_FUNCTION = [
    ("now()", "now()", "('2026-10-01T00:00:00Z'::timestamptz)"),
    ("current_timestamp", "current_timestamp", "('2026-10-01T00:00:00Z'::timestamptz)"),
    ("current_date", "current_date", "('2026-10-01'::date)"),
    ("localtimestamp", "localtimestamp", "('2026-10-01T00:00:00'::timestamp)"),
    ("statement_timestamp()", "statement_timestamp()", "('2026-10-01T00:00:00Z'::timestamptz)"),
    ("clock_timestamp()", "clock_timestamp()", "('2026-10-01T00:00:00Z'::timestamptz)"),
    (
        "transaction_timestamp()",
        "transaction_timestamp()",
        "('2026-10-01T00:00:00Z'::timestamptz)",
    ),
    ("current_time", "current_time", "('00:00:00+00'::timetz)"),
    ("localtime", "localtime", "('00:00:00'::time)"),
]


def pin(sql: str, now: datetime = FROZEN) -> Classification:
    return classify(sql, now=now)


@pytest.mark.parametrize(("function", "key", "literal"), EVERY_FUNCTION)
def test_every_clock_function_is_pinned(function: str, key: str, literal: str) -> None:
    result = pin(f"SELECT {function}")
    assert result.pinned_sql == f"SELECT {literal}"
    assert list(result.pinned_values) == [key]
    assert result.non_deterministic is True
    assert result.kind == Kind.READ


@pytest.mark.parametrize(
    ("function", "literal"),
    [
        ("now()", "('2026-10-01T13:05:09.123456Z'::timestamptz)"),
        ("current_date", "('2026-10-01'::date)"),
        ("localtimestamp", "('2026-10-01T13:05:09.123456'::timestamp)"),
        ("current_time", "('13:05:09.123456+00'::timetz)"),
    ],
)
def test_microseconds_are_kept(function: str, literal: str) -> None:
    assert pin(f"SELECT {function}", WITH_MICROSECONDS).pinned_sql == f"SELECT {literal}"


def test_a_non_utc_clock_is_converted() -> None:
    from datetime import timedelta, timezone

    at = datetime(2026, 10, 1, 2, tzinfo=timezone(timedelta(hours=2)))
    assert pin("SELECT now()", at).pinned_sql == "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT NOW()", "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"),
        ('SELECT "now"()', "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"),
        ("SELECT pg_catalog.now()", "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"),
        ("SELECT now ( )", "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"),
        ("SELECT CURRENT_TIMESTAMP", "SELECT ('2026-10-01T00:00:00Z'::timestamptz)"),
        ("SELECT current_timestamp(3)", "SELECT ('2026-10-01T00:00:00Z'::timestamptz(3))"),
        ("SELECT localtime(2)", "SELECT ('00:00:00'::time(2))"),
        ("SELECT now()::date", "SELECT ('2026-10-01T00:00:00Z'::timestamptz)::date"),
        (
            "SELECT now() AT TIME ZONE 'UTC'",
            "SELECT ('2026-10-01T00:00:00Z'::timestamptz) AT TIME ZONE 'UTC'",
        ),
    ],
)
def test_spellings_and_precision(sql: str, expected: str) -> None:
    assert pin(sql).pinned_sql == expected


def test_several_uses_share_one_instant_and_one_entry() -> None:
    result = pin("SELECT now(), now(), current_date")
    assert result.pinned_sql.count("2026-10-01T00:00:00Z") == 2
    assert result.pinned_values == {
        "now()": "2026-10-01T00:00:00Z",
        "current_date": "2026-10-01",
    }


def test_precision_gets_its_own_entry() -> None:
    result = pin("SELECT current_timestamp, current_timestamp(3)")
    assert set(result.pinned_values) == {"current_timestamp", "current_timestamp(3)"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'now()'",
        "SELECT 'current_date'",
        "SELECT 1 /* now() */",
        "SELECT 1 -- current_timestamp",
        "SELECT now FROM events",
        "SELECT e.now, e.current_date FROM events e",
        'SELECT "current_date" FROM events',
        "SELECT 1 AS now",
    ],
)
def test_things_that_are_not_the_clock_are_left_alone(sql: str) -> None:
    result = pin(sql)
    assert result.pinned_sql == sql
    assert result.pinned_values == {}


@pytest.mark.parametrize(
    "sql",
    [
        "select   *\nfrom USERS -- keep me\n where  id=1;",
        "SELECT /* c */ 1",
        "DELETE FROM users WHERE id = 1  ",
        "  SELECT 'x'  ",
    ],
)
def test_sql_with_nothing_to_pin_comes_back_byte_for_byte(sql: str) -> None:
    assert pin(sql).pinned_sql == sql


def test_comments_and_whitespace_around_a_pinned_function_survive() -> None:
    sql = "DELETE FROM users -- why\nWHERE last_login_at < /* when */ now()  - interval '1 day'"
    expected = (
        "DELETE FROM users -- why\nWHERE last_login_at < /* when */ "
        "('2026-10-01T00:00:00Z'::timestamptz)  - interval '1 day'"
    )
    assert pin(sql).pinned_sql == expected


def test_the_replit_delete_pins_exactly() -> None:
    result = pin("DELETE FROM users WHERE last_login_at < now() - interval '6 months'")
    assert result.pinned_sql == (
        "DELETE FROM users WHERE last_login_at < ('2026-10-01T00:00:00Z'::timestamptz)"
        " - interval '6 months'"
    )
    assert result.pinned_values == {"now()": "2026-10-01T00:00:00Z"}
    assert result.kind == Kind.WRITE
    assert result.volatile_in_where is False  # pinned, so the same at preview and commit


def test_the_frozen_clock_fixture_pins_to_the_anchor(frozen_clock: FrozenClock) -> None:
    result = pin("SELECT now()", frozen_clock.now())
    assert result.pinned_values == {"now()": "2026-10-01T00:00:00Z"}


def test_pinning_does_not_apply_to_ddl() -> None:
    sql = "ALTER TABLE t ALTER COLUMN c SET DEFAULT now()"
    result = pin(sql)
    assert result.kind == Kind.DDL
    assert result.pinned_sql == sql


def test_another_schemas_now_is_not_the_clock_and_fails_closed() -> None:
    result = pin("SELECT other.now()")
    assert (result.kind, result.reason) == (Kind.UNKNOWN, "pin_check_failed")


def test_a_time_function_the_pinning_missed_fails_closed() -> None:
    # `current_date(3)` is not valid Postgres, so it is not spliced; the check must notice.
    result = pin("SELECT current_date(3)")
    assert result.kind == Kind.UNKNOWN


def test_the_reparse_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_pin(sql: str, tokens: Any, now: datetime) -> tuple[str, dict[str, str]]:
        return sql.replace("now()", "now() AND false"), {"now()": "x"}

    monkeypatch.setattr(classifier, "_pin", broken_pin)
    result = pin("DELETE FROM users WHERE a < now()")
    assert (result.kind, result.reason) == (Kind.UNKNOWN, "pin_check_failed")
    assert result.pinned_sql == "DELETE FROM users WHERE a < now()"


def test_a_splice_that_changes_the_statement_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def hostile_pin(sql: str, tokens: Any, now: datetime) -> tuple[str, dict[str, str]]:
        return sql.replace("now()", "1; DROP TABLE users"), {}

    monkeypatch.setattr(classifier, "_pin", hostile_pin)
    assert pin("SELECT now()").kind == Kind.UNKNOWN


def test_a_splice_that_leaves_a_clock_function_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(classifier, "_pin", lambda sql, tokens, now: (sql + " ", {}))
    assert pin("SELECT now()").reason == "pin_check_failed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT now()",
        "DELETE FROM users WHERE last_login_at < now() - interval '6 months'",
        "UPDATE support_tickets SET closed_at = now() WHERE id = 4803",
        "INSERT INTO customer_notes (user_id, body, created_at) VALUES (1, 'x', now())",
        "INSERT INTO t (a) VALUES (current_date), (localtimestamp)",
        "WITH d AS (DELETE FROM t WHERE c < current_timestamp RETURNING id) SELECT * FROM d",
        "SELECT * FROM orders WHERE created_at > current_date - 30 ORDER BY id LIMIT 5",
    ],
)
def test_reclassifying_the_pinned_sql_gives_the_same_flags(sql: str) -> None:
    first = pin(sql)
    second = pin(first.pinned_sql)
    assert second.pinned_sql == first.pinned_sql
    assert second.pinned_values == {}
    for name in classifier._PIN_CHECKED_FIELDS:
        assert getattr(second, name) == getattr(first, name), name
    assert first.non_deterministic is True
    assert second.non_deterministic is False


def test_a_pinned_value_in_fast_lane_values_keeps_it_eligible() -> None:
    result = pin("INSERT INTO customer_notes (user_id, body, created_at) VALUES (1, 'x', now())")
    assert result.fast_lane_eligible is True
    assert result.non_deterministic is True
