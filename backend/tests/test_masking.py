"""PII masking: the text heuristics and the classifier's `sql_display`."""

import time
from datetime import UTC, datetime

import pytest

from app.services.classifier import classify
from app.services.masking import SENSITIVE_NAME_RE, looks_like_pii, mask_text

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def display(sql: str) -> str:
    return classify(sql, now=NOW).sql_display


@pytest.mark.parametrize(
    "text",
    [
        "jane.doe@example.com",
        "call jane+tag@sub.example.co.uk now",
        "555-123-4567",
        "(555) 123-4567",
        "+1 555 123 4567",
        "+44 20 7946 0958",
        "5551234567",
    ],
)
def test_text_that_looks_like_pii(text: str) -> None:
    assert looks_like_pii(text)


@pytest.mark.parametrize(
    "text",
    [
        "%@example.com",
        "2026-10-01",
        "2026-10-01T00:00:00Z",
        "2026-10-01T00:00:00.123456+00:00",
        "2026-10-01 12:30:45",
        "550e8400-e29b-41d4-a716-446655440000",
        "12345678-1234-1234-1234-123456789012",
        "Called about renewal; will upgrade next month.",
        "12 months",
        "1234",
    ],
)
def test_text_that_is_not_pii(text: str) -> None:
    assert not looks_like_pii(text)
    assert mask_text(text) == text


def test_mask_text_replaces_only_the_match() -> None:
    assert mask_text("mail a@b.com or 555-123-4567 today") == "mail [email] or [phone] today"


@pytest.mark.parametrize(
    "name", ["email", "user_email", "Phone", "billing_address", "password_hash"]
)
def test_sensitive_names(name: str) -> None:
    assert SENSITIVE_NAME_RE.search(name)


@pytest.mark.parametrize("name", ["classname", "id", "plan", "status"])
def test_names_that_are_not_sensitive(name: str) -> None:
    assert not SENSITIVE_NAME_RE.search(name)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        pytest.param(
            "UPDATE users SET plan = 'free' WHERE name = 'jane@example.com'",
            "UPDATE users SET plan = 'free' WHERE name = '[email]'",
            id="email-literal",
        ),
        pytest.param(
            "INSERT INTO customer_notes (user_id, body)"
            " VALUES (1, 'write to jane@example.com asap')",
            "INSERT INTO customer_notes (user_id, body) VALUES (1, '[email]')",
            id="email-inside-longer-literal",
        ),
        pytest.param(
            "INSERT INTO customer_notes (user_id, body) VALUES (1, 'call 555-123-4567')",
            "INSERT INTO customer_notes (user_id, body) VALUES (1, '[phone]')",
            id="phone-literal",
        ),
        pytest.param(
            "DELETE FROM users WHERE id = 1 -- ask jane@example.com",
            "DELETE FROM users WHERE id = 1 -- ask [email]",
            id="email-in-line-comment",
        ),
        pytest.param(
            "DELETE /* 555-123-4567 */ FROM users WHERE id = 1",
            "DELETE /* [phone] */ FROM users WHERE id = 1",
            id="phone-in-block-comment",
        ),
        pytest.param(
            "SELECT $$jane@example.com$$",
            "SELECT '[email]'",
            id="dollar-quoted-string",
        ),
        pytest.param(
            "INSERT INTO users (email, plan) VALUES ('hidden', 'free')",
            "INSERT INTO users (email, plan) VALUES ('[masked]', 'free')",
            id="insert-column-position",
        ),
        pytest.param(
            "INSERT INTO users (email, plan) VALUES ('a', 'free'), ('b', 'pro')",
            "INSERT INTO users (email, plan) VALUES ('[masked]', 'free'), ('[masked]', 'pro')",
            id="insert-several-rows",
        ),
        pytest.param(
            "UPDATE users SET phone = 'xyz' WHERE id = 1",
            "UPDATE users SET phone = '[masked]' WHERE id = 1",
            id="set-assignment",
        ),
        pytest.param(
            "UPDATE users SET plan = 'free' WHERE password_hash = 'abc'",
            "UPDATE users SET plan = 'free' WHERE password_hash = '[masked]'",
            id="equality",
        ),
        pytest.param(
            "DELETE FROM users WHERE email IN ('a', 'b')",
            "DELETE FROM users WHERE email IN ('[masked]', '[masked]')",
            id="in-list",
        ),
        pytest.param(
            "DELETE FROM users WHERE email LIKE '%corp%'",
            "DELETE FROM users WHERE email LIKE '[masked]'",
            id="like",
        ),
        pytest.param(
            "DELETE FROM users WHERE email NOT ILIKE 'zed%'",
            "DELETE FROM users WHERE email NOT ILIKE '[masked]'",
            id="not-ilike",
        ),
        pytest.param(
            "DELETE FROM users WHERE 'abc' = password_hash",
            "DELETE FROM users WHERE '[masked]' = password_hash",
            id="literal-on-the-left",
        ),
    ],
)
def test_sql_display_masks(sql: str, expected: str) -> None:
    assert display(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users WHERE created_at > '2026-10-01'",
        "SELECT * FROM users WHERE created_at > '2026-10-01T00:00:00Z'",
        "SELECT * FROM users WHERE id = '550e8400-e29b-41d4-a716-446655440000'",
        "SELECT * FROM users WHERE name LIKE '%@example.com'",
        "INSERT INTO customer_notes (user_id, body) VALUES (42, 'Called about renewal.')",
    ],
)
def test_sql_display_keeps(sql: str) -> None:
    assert display(sql) == sql


def test_pinned_values_stay_visible() -> None:
    result = classify("DELETE FROM users WHERE last_login_at < now()", now=NOW)
    assert "'2026-10-01T00:00:00Z'::timestamptz" in result.sql_display
    assert result.sql_display == result.pinned_sql


def test_unparseable_sql_is_still_masked() -> None:
    result = classify("SELEC 'jane@example.com' FROM", now=NOW)
    assert result.sql_display == "SELEC '[email]' FROM"


def test_text_that_will_not_tokenize_is_masked_by_regex() -> None:
    result = classify(r"SELECT E'a\'b', 'jane@example.com'", now=NOW)
    assert "jane@example.com" not in result.sql_display


def test_comment_only_sql_is_masked() -> None:
    assert display("-- jane@example.com") == "-- [email]"


def test_classify_never_changes_its_input() -> None:
    sql = "UPDATE users SET email = 'jane@example.com' WHERE id = now()::int"
    before = str(sql)
    result = classify(sql, now=NOW)
    assert sql == before
    assert "jane@example.com" in sql
    assert "jane@example.com" not in result.sql_display


@pytest.mark.parametrize("filler", ["x", "1 ", "a.", "-", "@", "(1"])
def test_masking_a_long_literal_is_fast(filler: str) -> None:
    text = filler * (20_000 // len(filler))
    started = time.perf_counter()
    mask_text(text)
    looks_like_pii(text)
    assert time.perf_counter() - started < 1.0


def test_email_inside_a_long_run_is_found() -> None:
    assert mask_text("x" * 30 + "@example.com tail") == "[email] tail"
