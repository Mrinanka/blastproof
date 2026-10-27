"""Step 16: the commit statement. Pure: no database (integration tests run it on PostgreSQL 18)."""

import pytest

from app.services.commit_sql import CommitUnsupportedError, commit_statement

ALIASES = ("bp_o_ab12", "bp_n_ab12")
TAIL = 'RETURNING WITH (OLD AS "bp_o_ab12", NEW AS "bp_n_ab12")'
MERGE = "MERGE INTO feature_flags t USING (SELECT 'k' AS key) s ON t.key = s.key"
UPSERT = (
    "INSERT INTO feature_flags (key) VALUES ('k') ON CONFLICT (key) DO UPDATE SET enabled = true"
)


def build(sql: str, pk: tuple[str, ...] = ("id",)) -> str:
    return commit_statement(sql, pk, ALIASES)


def body(statement: str) -> str:
    """The approved write: what stands before the appended RETURNING."""
    head, tail = statement.split("\n" + TAIL)
    assert tail.startswith(" CASE WHEN")
    return head


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DELETE FROM users WHERE id = 1", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1;", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1 ;  \n", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1 -- why", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1; -- why", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1 /* x */", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1 RETURNING *", "DELETE FROM users WHERE id = 1"),
        ("DELETE FROM users WHERE id = 1 returning id, email;", "DELETE FROM users WHERE id = 1"),
        (
            "UPDATE users SET plan = 'pro' WHERE id = 1 RETURNING id",
            "UPDATE users SET plan = 'pro' WHERE id = 1",
        ),
        (
            "INSERT INTO products (sku, name) VALUES ('a', 'b') RETURNING id",
            "INSERT INTO products (sku, name) VALUES ('a', 'b')",
        ),
        (UPSERT, UPSERT),
        (UPSERT + " RETURNING id", UPSERT),
        (
            "WITH old AS (SELECT id FROM users WHERE plan = 'free')"
            " DELETE FROM users WHERE id IN (SELECT id FROM old)",
            "WITH old AS (SELECT id FROM users WHERE plan = 'free')"
            " DELETE FROM users WHERE id IN (SELECT id FROM old)",
        ),
        (
            MERGE + " WHEN MATCHED THEN UPDATE SET enabled = true",
            MERGE + " WHEN MATCHED THEN UPDATE SET enabled = true",
        ),
        (
            MERGE + " WHEN MATCHED THEN DELETE RETURNING t.id",
            MERGE + " WHEN MATCHED THEN DELETE",
        ),
    ],
)
def test_the_agents_returning_is_replaced_and_the_write_is_kept(sql: str, expected: str) -> None:
    assert body(build(sql)) == expected


def test_a_trailing_line_comment_cannot_swallow_the_returning_clause() -> None:
    statement = build("DELETE FROM users WHERE id = 1 -- why")
    assert "\nRETURNING WITH" in statement
    assert statement.splitlines()[0] == "DELETE FROM users WHERE id = 1"


def test_the_returning_word_in_a_string_or_a_quoted_name_is_not_the_clause() -> None:
    sql = "UPDATE users SET full_name = 'RETURNING *' WHERE id = 1"
    assert body(build(sql)) == sql
    sql = 'UPDATE feature_flags SET "returning" = 1 WHERE id = 1'
    assert body(build(sql)) == sql
    sql = "UPDATE users SET full_name = 'x -- RETURNING y' WHERE id = 1 RETURNING id"
    assert body(build(sql)) == "UPDATE users SET full_name = 'x -- RETURNING y' WHERE id = 1"


def test_the_returning_clause_reports_both_sides_with_the_given_aliases() -> None:
    statement = build("DELETE FROM users WHERE id = 1", ("id",))
    assert statement.count("RETURNING") == 1
    assert 'jsonb_build_object(\'id\', "bp_o_ab12"."id")' in statement
    assert 'jsonb_build_object(\'id\', "bp_n_ab12"."id")' in statement
    assert 'md5(row_to_json("bp_o_ab12")::text)' in statement
    assert 'to_jsonb("bp_n_ab12")' in statement


def test_a_composite_key_reports_every_column() -> None:
    statement = build("DELETE FROM links WHERE a = 1", ("a", "b"))
    assert '\'a\', "bp_o_ab12"."a", \'b\', "bp_o_ab12"."b"' in statement


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT * FROM users", "not_a_plain_write"),
        (
            "WITH d AS (DELETE FROM users WHERE plan = 'free' RETURNING id) SELECT * FROM d",
            "not_a_plain_write",
        ),
        (
            "WITH d AS (DELETE FROM users WHERE plan = 'free' RETURNING id)"
            " DELETE FROM orders WHERE user_id IN (SELECT id FROM d)",
            "data_modifying_subquery",
        ),
        (
            "WITH d AS (DELETE FROM users WHERE plan = 'free' RETURNING id)"
            " INSERT INTO customer_notes (user_id, body) SELECT id, 'x' FROM d",
            "data_modifying_subquery",
        ),
        ("TRUNCATE users", "not_a_plain_write"),
        ("DROP TABLE users", "not_a_plain_write"),
        ("BEGIN", "not_a_plain_write"),
        ("DELETE FROM users WHERE id = 1; DELETE FROM users WHERE id = 2", "multiple_statements"),
        ("DELETE FROM users WHERE id = 1; SELECT 1", "multiple_statements"),
        ("", "multiple_statements"),
        (";", "multiple_statements"),
        ("DELETE FROM", "unparseable"),
    ],
)
def test_shapes_the_commit_cannot_check_are_refused(sql: str, reason: str) -> None:
    with pytest.raises(CommitUnsupportedError) as raised:
        build(sql)
    assert raised.value.reason == reason


def test_a_table_without_a_primary_key_is_refused() -> None:
    with pytest.raises(CommitUnsupportedError) as raised:
        build("DELETE FROM users WHERE id = 1", ())
    assert raised.value.reason == "no_primary_key"


def test_a_cut_that_changes_the_statement_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cut text must parse to the original without its RETURNING. Cut somewhere else and the
    check says so."""
    from app.services import commit_sql

    real = commit_sql.sqlglot.parse

    def tampered(sql: str, **kwargs: str) -> list:
        if "RETURNING" not in sql.upper():  # the second parse, of the cut text
            return real("DELETE FROM users WHERE id = 2", **kwargs)
        return real(sql, **kwargs)

    monkeypatch.setattr(commit_sql.sqlglot, "parse", tampered)
    with pytest.raises(CommitUnsupportedError) as raised:
        build("DELETE FROM users WHERE id = 1 RETURNING *")
    assert raised.value.reason == "cut_changed"
