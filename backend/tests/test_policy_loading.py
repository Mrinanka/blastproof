"""Step 9: the rules file loads into a closed, validated schema, or the app does not start."""

import textwrap
from pathlib import Path

import pytest

from app.config import DEFAULT_POLICY_FILE, Settings
from app.main import create_app
from app.services.policy import PolicyFileError, check_columns, load_policy
from tests.conftest import NetworkGuard

BE_44_RULES = [
    "No DDL",
    "Unparseable SQL",
    "No DROP or TRUNCATE",
    "One statement only",
    "No transaction control",
    "Hands off Blastproof's schema",
    "No side-effect functions",
    "Volatile values in WHERE",
    "Unordered LIMIT",
    "Delete or update without WHERE",
    "Every row of a table",
    "Most of a table",
    "Large change",
    "Touches paying customers",
    "Fast-lane note",
    "Small insert",
]
SEED_COLUMNS = {"users": {"id", "plan", "email", "phone", "full_name"}}


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def one_rule(when: str, then: str = "block", reason: str = "Because.") -> str:
    return textwrap.dedent(
        f"""
        rules:
          - name: R
            when: {when}
            then: {then}
            reason: {reason}
        """
    )


def test_the_shipped_file_loads_in_plan_order() -> None:
    policy = load_policy(DEFAULT_POLICY_FILE)
    assert [r.name for r in policy.rules] == BE_44_RULES
    assert policy.important_columns == {"users": ["plan"]}
    assert policy.sensitive_columns == {"users": ["email", "phone", "full_name"]}
    assert policy.derived_groups["users"]["paying"].columns == {"plan"}


def test_the_unparseable_reason_is_the_clearer_text() -> None:
    policy = load_policy(DEFAULT_POLICY_FILE)
    unparseable = next(r for r in policy.rules if r.name == "Unparseable SQL")
    assert "does not support" in unparseable.reason


def test_a_minimal_file_loads(tmp_path: Path) -> None:
    policy = load_policy(write(tmp_path, one_rule("{ kind_in: [ddl] }")))
    assert [r.name for r in policy.rules] == ["R"]


TWO_A = """
    rules:
      - { name: A, when: { kind_in: [ddl] }, then: block, reason: x }
      - { name: A, when: { kind_in: [read] }, then: block, reason: y }
    """
EXTRA_FIELD = """
    rules:
      - name: R
        when: { kind_in: [ddl] }
        then: block
        reason: x
        colour: red
    """
EMPTY_REASON = """
    rules:
      - { name: A, when: { kind_in: [ddl] }, then: block, reason: "" }
    """
EMPTY_NAME = """
    rules:
      - { name: "", when: { kind_in: [ddl] }, then: block, reason: x }
    """


@pytest.mark.parametrize(
    ("text", "fragments"),
    [
        (one_rule("{ nonsense: true }"), ["rule 'R'", "nonsense"]),
        (EXTRA_FIELD, ["rule 'R'", "colour"]),
        (one_rule("{ kind_in: [ddl] }") + "\nextra_section: 1\n", ["extra_section"]),
        (one_rule("{}"), ["rule 'R'", "at least one condition"]),
        (one_rule("{ kind_in: [ddl] }", then="maybe"), ["rule 'R'", "then"]),
        (TWO_A, ["'A'", "twice"]),
        (EMPTY_REASON, ["rule 'A'", "reason"]),
        (EMPTY_NAME, ["rule #1", "name"]),
        (one_rule('{ multi_statement: "true" }'), ["rule 'R'", "multi_statement"]),
        (one_rule("{ rows_affected_gt: true }"), ["rule 'R'", "rows_affected_gt"]),
        (one_rule("{ statement_in: [delte] }"), ["rule 'R'", "statement_in", "delte"]),
        (one_rule("{ kind_in: [writ] }"), ["rule 'R'", "kind_in", "writ"]),
        (one_rule("{ statement_in: [] }"), ["rule 'R'", "statement_in", "empty"]),
        (one_rule("{ has_where: null }"), ["rule 'R'", "has_where"]),
        (one_rule("{ rows_affected_gt: -1 }"), ["rule 'R'", "rows_affected_gt"]),
        (one_rule("{ fraction_of_table_gt: 1.5 }"), ["rule 'R'", "fraction_of_table_gt"]),
        (one_rule("{ tables_any: [Users] }"), ["rule 'R'", "tables_any", "Users"]),
        (one_rule("{ derived_group_gt: { paying: 0 } }"), ["rule 'R'", "table.group"]),
        (one_rule("{ derived_group_gt: { users.paying: -1 } }"), ["rule 'R'", "users.paying"]),
        (
            one_rule("{ fast_lane: true, tables_any: [notes] }", then="review"),
            ["rule 'R'", "fast_lane", "allows"],
        ),
        (one_rule("{ fast_lane: true }", then="allow"), ["rule 'R'", "fast_lane", "tables_any"]),
        (
            one_rule(
                "{ fast_lane: true, tables_any: [notes], rows_affected_lte: 5 }", then="allow"
            ),
            ["rule 'R'", "fast_lane", "diff key"],
        ),
        (
            one_rule("{ derived_group_gt: { users.paying: 0 } }", then="review"),
            ["rule 'R'", "users.paying", "not defined"],
        ),
    ],
)
def test_a_bad_rules_file_names_the_rule_and_key(
    tmp_path: Path, text: str, fragments: list[str]
) -> None:
    with pytest.raises(PolicyFileError) as raised:
        load_policy(write(tmp_path, text))
    for fragment in fragments:
        assert fragment in str(raised.value)


def test_a_duplicate_key_in_when_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        rules:
          - name: R
            when:
              kind_in: [ddl]
              kind_in: [unknown]
            then: block
            reason: x
        """,
    )
    with pytest.raises(PolicyFileError, match=r"duplicate key 'kind_in' on line 6"):
        load_policy(path)


def test_a_duplicate_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, one_rule("{ kind_in: [ddl] }") + "\nrules: []\n")
    with pytest.raises(PolicyFileError, match="duplicate key 'rules'"):
        load_policy(path)


def with_group(condition: str) -> str:
    return one_rule("{ kind_in: [ddl] }") + f'\nderived_groups:\n  users:\n    g: "{condition}"\n'


@pytest.mark.parametrize(
    ("condition", "fragment"),
    [
        ("lower(plan) = 'pro'", "Lower"),
        ("plan in (select plan from other)", "Subquery"),
        ("plan = 'pro'; drop table users", "';'"),
        ("plan = 'pro' ) (", "parse"),
        ("plan::text = 'pro'", "Cast"),
        ("users.plan = 'pro'", "table name"),
        ("plan", "true-or-false"),
        ("'a' = 'b'", "at least one column"),
        ("pg_sleep(1) is null", "may not contain"),
    ],
)
def test_a_derived_group_condition_must_be_plain(
    tmp_path: Path, condition: str, fragment: str
) -> None:
    with pytest.raises(PolicyFileError) as raised:
        load_policy(write(tmp_path, with_group(condition)))
    assert "derived_groups" in str(raised.value)
    assert fragment in str(raised.value)


@pytest.mark.parametrize(
    "condition",
    [
        "plan in ('pro', 'enterprise')",
        "plan = 'pro' or plan = 'enterprise'",
        "not (plan is null)",
        "plan is not null and seats >= 5",
        "balance > -1",
    ],
)
def test_allowed_derived_group_conditions_load(tmp_path: Path, condition: str) -> None:
    policy = load_policy(write(tmp_path, with_group(condition)))
    assert policy.derived_groups["users"]["g"].condition == condition


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyFileError, match="cannot read"):
        load_policy(tmp_path / "nope.yaml")


@pytest.mark.parametrize("text", ["- a\n- b\n", "just text\n", ""])
def test_a_file_that_is_not_a_mapping_is_an_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(PolicyFileError, match="mapping"):
        load_policy(write(tmp_path, text))


def test_a_yaml_syntax_error_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyFileError, match="not valid YAML"):
        load_policy(write(tmp_path, "rules: [\n  - name: a\n"))


def test_an_unreadable_encoding_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(PolicyFileError, match="cannot read"):
        load_policy(path)


def test_the_error_never_repeats_the_input_value(tmp_path: Path) -> None:
    path = write(tmp_path, one_rule("{ rows_affected_gt: 123456789012345678 }", then="maybe"))
    with pytest.raises(PolicyFileError) as raised:
        load_policy(path)
    assert "maybe" not in str(raised.value)


# --- check_columns -----------------------------------------------------------------------------


def test_check_columns_passes_on_the_seed_columns() -> None:
    check_columns(load_policy(DEFAULT_POLICY_FILE), SEED_COLUMNS)


@pytest.mark.parametrize(
    ("columns", "fragment"),
    [
        ({"users": {"id", "email", "phone"}}, "users.plan"),
        ({"users": {"id", "plan", "phone"}}, "users.email"),
        ({"users": {"id", "plan", "email"}}, "users.phone"),
        ({"orders": {"id"}}, "table 'users' does not exist"),
    ],
)
def test_check_columns_names_what_is_missing(columns: dict[str, set[str]], fragment: str) -> None:
    with pytest.raises(PolicyFileError, match="does not match the database") as raised:
        check_columns(load_policy(DEFAULT_POLICY_FILE), columns)
    assert fragment in str(raised.value)


def test_check_columns_checks_a_derived_group_column(tmp_path: Path) -> None:
    policy = load_policy(write(tmp_path, with_group("tier = 'gold'")))
    with pytest.raises(PolicyFileError, match=r"users\.tier"):
        check_columns(policy, SEED_COLUMNS)


def test_check_columns_checks_an_important_column(tmp_path: Path) -> None:
    text = one_rule("{ kind_in: [ddl] }") + "\nimportant_columns:\n  users: [tier]\n"
    with pytest.raises(PolicyFileError, match=r"users\.tier"):
        check_columns(load_policy(write(tmp_path, text)), SEED_COLUMNS)


# --- is_sensitive ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    ["email", "phone", "ssn", "address", "token", "password", "user_email", "ip_address", "Phone"],
)
def test_names_that_look_like_personal_data_are_sensitive(column: str) -> None:
    assert load_policy(DEFAULT_POLICY_FILE).is_sensitive("anything", column)


def test_a_configured_column_is_sensitive(tmp_path: Path) -> None:
    text = one_rule("{ kind_in: [ddl] }") + "\nsensitive_columns:\n  users: [nickname]\n"
    policy = load_policy(write(tmp_path, text))
    assert policy.is_sensitive("users", "nickname")
    assert not policy.is_sensitive("orders", "nickname")


@pytest.mark.parametrize("column", ["plan", "classname", "id", "created_at"])
def test_ordinary_columns_are_not_sensitive(column: str) -> None:
    assert not load_policy(DEFAULT_POLICY_FILE).is_sensitive("users", column)


# --- important columns may not be sensitive (step 12) ------------------------------------------


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        (
            "important_columns:\n  users: [plan, nickname]\n"
            "sensitive_columns:\n  users: [nickname]\n",
            "users.nickname",
        ),
        ("important_columns:\n  users: [email]\n", "users.email"),
        ("important_columns:\n  orders: [billing_address]\n", "orders.billing_address"),
    ],
    ids=["configured", "by-name", "by-name-inside"],
)
def test_a_sensitive_important_column_is_refused(tmp_path: Path, extra: str, named: str) -> None:
    """Breakdown labels are raw values: they reach the prompt, actions.impact and the UI."""
    with pytest.raises(PolicyFileError) as raised:
        load_policy(write(tmp_path, one_rule("{ kind_in: [ddl] }") + "\n" + extra))
    message = str(raised.value)
    assert "important_columns" in message and named in message and "sensitive" in message


def test_an_ordinary_important_column_on_a_table_with_sensitive_ones_loads(
    tmp_path: Path,
) -> None:
    text = one_rule("{ kind_in: [ddl] }") + (
        "\nimportant_columns:\n  users: [plan]\nsensitive_columns:\n  users: [nickname]\n"
    )
    assert load_policy(write(tmp_path, text)).important_columns == {"users": ["plan"]}


# --- startup -----------------------------------------------------------------------------------


def test_create_app_loads_the_shipped_policy(settings: Settings) -> None:
    app = create_app(settings)
    assert [r.name for r in app.state.policy.rules] == BE_44_RULES


def test_create_app_uses_the_configured_file(tmp_path: Path) -> None:
    path = write(tmp_path, one_rule("{ kind_in: [ddl] }"))
    app = create_app(Settings(_env_file=None, app_env="test", policy_file=path))
    assert [r.name for r in app.state.policy.rules] == ["R"]


def test_create_app_refuses_a_bad_file(tmp_path: Path) -> None:
    path = write(tmp_path, one_rule("{ nonsense: true }"))
    with pytest.raises(PolicyFileError, match="nonsense"):
        create_app(Settings(_env_file=None, app_env="test", policy_file=path))


def test_policy_file_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path, one_rule("{ kind_in: [ddl] }"))
    monkeypatch.setenv("POLICY_FILE", str(path))
    assert Settings(_env_file=None, app_env="test").policy_file == path


def test_startup_with_the_policy_stays_offline(
    settings: Settings, no_network: NetworkGuard
) -> None:
    create_app(settings)
