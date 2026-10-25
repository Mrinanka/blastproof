"""D-103: the temporary read check lets through one plain SELECT and nothing else.

It is the first of four layers (then the reader role, a READ ONLY transaction and the statement
timeout); step 8's classifier replaces it.
"""

import pytest

from app.services.reads import MAX_SQL_BYTES, check_read

ACCEPTED = [
    "SELECT 1",
    "select count(*) from users where plan = 'pro'",
    "WITH paying AS (SELECT * FROM users WHERE plan <> 'free') SELECT count(*) FROM paying",
    "SELECT id FROM users UNION SELECT user_id FROM orders",
    "SELECT 1 INTERSECT SELECT 1",
    "-- how many?\nSELECT count(*) FROM users",
    "/* leading */ SELECT 1 /* trailing */",
    "SELECT 1;",
    "SELECT 1 ;  ",
    "SELECT pg_sleep(1)",  # slow but harmless: the statement timeout handles it
    "SELECT * FROM users u JOIN orders o ON o.user_id = u.id LIMIT 10",
]

REFUSED = [
    ("INSERT INTO customer_notes (user_id, body) VALUES (1, 'x')", "not_a_select"),
    ("UPDATE users SET plan = 'free'", "not_a_select"),
    ("DELETE FROM users", "not_a_select"),
    (
        "MERGE INTO users u USING orders o ON u.id = o.user_id WHEN MATCHED THEN DELETE",
        "not_a_select",
    ),
    ("WITH gone AS (DELETE FROM users RETURNING id) SELECT count(*) FROM gone", "not_a_plain_read"),
    (
        "WITH x AS (UPDATE users SET plan = 'free' RETURNING id) SELECT * FROM x",
        "not_a_plain_read",
    ),
    ("SELECT * INTO users_copy FROM users", "not_a_plain_read"),
    ("SELECT * FROM users FOR UPDATE", "not_a_plain_read"),
    ("SELECT * FROM users FOR NO KEY UPDATE", "not_a_plain_read"),
    ("SELECT * FROM users FOR SHARE", "not_a_plain_read"),
    ("SELECT * FROM users FOR KEY SHARE", "not_a_plain_read"),
    ("EXPLAIN SELECT 1", "not_a_select"),
    ("EXPLAIN ANALYZE DELETE FROM users", "not_a_select"),  # ANALYZE runs the statement
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("SELECT 1; DELETE FROM users", "multiple_statements"),
    ("BEGIN", "not_a_select"),
    ("COMMIT", "not_a_select"),
    ("SET statement_timeout = 0", "not_a_select"),
    ("COPY users TO STDOUT", "not_a_select"),
    ("DO $$ BEGIN DELETE FROM users; END $$", "not_a_select"),
    ("CALL cleanup()", "not_a_select"),
    ("TABLE users", "not_a_select"),
    ("", "empty"),
    ("   ", "empty"),
    ("-- just a comment", "empty"),
    (";", "empty"),
    ("SELEC 1", "unparseable"),
    ("SELECT (1", "unparseable"),
    ("SELECT pg_terminate_backend(123)", "side_effect_function"),
    ("SELECT pg_catalog.pg_terminate_backend(pid) FROM pg_stat_activity", "side_effect_function"),
    ('SELECT "pg_cancel_backend"(1)', "side_effect_function"),
    ("SELECT pg_advisory_lock(1)", "side_effect_function"),
    ("SELECT pg_advisory_xact_lock(1)", "side_effect_function"),
    ("SELECT pg_try_advisory_lock(1)", "side_effect_function"),
    ("SELECT set_config('statement_timeout', '0', false)", "side_effect_function"),
    ("SELECT nextval('users_id_seq')", "side_effect_function"),
    ("SELECT setval('users_id_seq', 1)", "side_effect_function"),
    ("SELECT pg_notify('channel', 'hi')", "side_effect_function"),
    ("SELECT dblink_exec('host=x', 'DELETE FROM users')", "side_effect_function"),
    ("SELECT lo_import('/etc/passwd')", "side_effect_function"),
    ("SELECT id FROM users WHERE id IN (SELECT PG_TERMINATE_BACKEND(1))", "side_effect_function"),
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_plain_reads_are_accepted(sql: str) -> None:
    check = check_read(sql)
    assert check.ok, check.reason
    assert check.reason is None


@pytest.mark.parametrize(("sql", "reason"), REFUSED)
def test_anything_else_is_refused(sql: str, reason: str) -> None:
    check = check_read(sql)
    assert not check.ok
    assert check.reason == reason


def test_over_20_kb_is_refused_before_parsing() -> None:
    padding = " " * (MAX_SQL_BYTES - len("SELECT 1"))
    assert check_read("SELECT 1" + padding).ok
    assert check_read("SELECT 1" + padding + " ").reason == "too_long"
    # Bytes, not characters: multi-byte text counts for what it weighs.
    assert check_read("SELECT '" + "é" * (MAX_SQL_BYTES // 2) + "'").reason == "too_long"


def test_tables_are_listed_without_cte_names() -> None:
    check = check_read(
        "WITH paying AS (SELECT * FROM users) SELECT * FROM paying JOIN public.orders o ON true"
    )
    # Names as written: the schema is kept when the SQL gives one.
    assert check.tables == ["public.orders", "users"]
