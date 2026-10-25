"""Step 9: each condition key, each shipped rule, the pass-1 skip and the strictest-wins logic."""

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.db.enums import Decision
from app.services.classifier import Classification, classify
from app.services.policy import (
    NO_RULE_REASON,
    DiffFacts,
    Policy,
    PolicyFactsError,
    Route,
    TableFacts,
    evaluate_pass_one,
    evaluate_pass_two,
    load_policy,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SHIPPED = load_policy(DEFAULT_POLICY_FILE)
NO_DIFF = DiffFacts()


def c(sql: str) -> Classification:
    return classify(sql, now=NOW)


def make_policy(*rules: dict[str, Any], **sections: Any) -> Policy:
    return Policy.model_validate({"rules": list(rules), **sections})


def r(name: str, when: dict[str, Any], then: str, reason: str | None = None) -> dict[str, Any]:
    return {"name": name, "when": when, "then": then, "reason": reason or f"{name} reason"}


def facts(
    deleted: int = 0,
    updated: int = 0,
    inserted: int = 0,
    before: int | None = 1000,
    table: str = "users",
    cascade: bool = False,
    groups: dict[str, int] | None = None,
    extra: dict[str, TableFacts] | None = None,
) -> DiffFacts:
    tables = {table: TableFacts(deleted, updated, inserted, before, cascade)}
    tables.update(extra or {})
    return DiffFacts(tables=tables, derived_groups=groups or {})


def names(result: Any) -> list[str]:
    return [m.name for m in result.matched]


# --- SQL-only keys: each matching and not matching -----------------------------------------------

DELETE_SQL = "DELETE FROM users WHERE id = 1"
INSERT_SQL = "INSERT INTO customer_notes (customer_id, body) VALUES (1, 'hi')"

SQL_KEY_CASES: list[tuple[str, Any, str, str]] = [
    # (key, value, sql that matches, sql that does not)
    ("statement_in", ["delete"], DELETE_SQL, "SELECT 1"),
    ("statement_in", ["delete", "update"], "UPDATE users SET plan = 'x' WHERE id = 1", "SELECT 1"),
    ("kind_in", ["write"], DELETE_SQL, "SELECT 1"),
    ("kind_in", ["ddl"], "CREATE TABLE t (a int)", DELETE_SQL),
    ("kind_in", ["unknown"], "VACUUM", DELETE_SQL),
    ("tables_any", ["users"], DELETE_SQL, "DELETE FROM orders WHERE id = 1"),
    (
        "tables_any",
        ["blastproof.applied_actions"],
        "SELECT * FROM blastproof.applied_actions",
        DELETE_SQL,
    ),
    ("schemas_any", ["blastproof"], "SELECT * FROM blastproof.applied_actions", DELETE_SQL),
    ("has_where", False, "DELETE FROM users", DELETE_SQL),
    ("has_where", True, DELETE_SQL, "DELETE FROM users"),
    ("multi_statement", True, "SELECT 1; SELECT 2", "SELECT 1"),
    ("transaction_control", True, "BEGIN", "SELECT 1"),
    ("volatile_in_where", True, "DELETE FROM users WHERE random() < 0.5", DELETE_SQL),
    (
        "unordered_limit",
        True,
        "DELETE FROM users WHERE id IN (SELECT id FROM users LIMIT 3)",
        DELETE_SQL,
    ),
    ("fast_lane", True, INSERT_SQL, DELETE_SQL),
    ("fast_lane", False, DELETE_SQL, INSERT_SQL),
    ("side_effect_function", True, "SELECT pg_terminate_backend(1)", "SELECT 1"),
]


@pytest.mark.parametrize(
    ("key", "value", "matching", "other"), SQL_KEY_CASES, ids=lambda v: str(v)[:30]
)
def test_each_sql_key_matches_and_does_not(key: str, value: Any, matching: str, other: str) -> None:
    when = {key: value}
    then = "block"
    if key == "fast_lane" and value is True:
        # The loader only accepts fast_lane: true on an allow rule that lists its tables.
        when, then = {**when, "tables_any": ["customer_notes"]}, "allow"
    policy = make_policy(r("R", when, then))
    assert names(evaluate_pass_one(policy, c(matching))) == ["R"]
    assert names(evaluate_pass_one(policy, c(other))) == []


def test_every_listed_sql_key_is_covered() -> None:
    from app.services.policy import SQL_KEYS

    assert {key for key, *_ in SQL_KEY_CASES} == SQL_KEYS


def test_all_conditions_in_a_rule_must_hold() -> None:
    policy = make_policy(r("R", {"statement_in": ["delete"], "tables_any": ["users"]}, "block"))
    assert names(evaluate_pass_one(policy, c(DELETE_SQL))) == ["R"]
    assert names(evaluate_pass_one(policy, c("DELETE FROM orders WHERE id = 1"))) == []
    assert names(evaluate_pass_one(policy, c("SELECT * FROM users"))) == []


def test_a_cte_delete_counts_for_statement_in() -> None:
    policy = make_policy(r("R", {"statement_in": ["delete"]}, "block"))
    sql = "WITH d AS (DELETE FROM users WHERE id = 1 RETURNING id) SELECT * FROM d"
    assert names(evaluate_pass_one(policy, c(sql))) == ["R"]


# --- Diff keys ------------------------------------------------------------------------------------

DIFF_KEY_CASES: list[tuple[str, Any, DiffFacts, DiffFacts]] = [
    ("rows_affected_gt", 10, facts(deleted=11), facts(deleted=10)),
    ("rows_affected_lte", 10, facts(inserted=10), facts(inserted=11)),
    ("fraction_of_table_gt", 0.5, facts(deleted=501), facts(deleted=500)),
    ("fraction_of_table_gte", 1.0, facts(deleted=1000), facts(deleted=999)),
    (
        "cascade_tables_any",
        ["orders"],
        facts(deleted=1, extra={"orders": TableFacts(deleted=2, rows_before=50, is_cascade=True)}),
        facts(deleted=1, extra={"orders": TableFacts(deleted=2, rows_before=50, is_cascade=False)}),
    ),
    (
        "derived_group_gt",
        {"users.paying": 0},
        facts(updated=3, groups={"users.paying": 1}),
        facts(updated=3, groups={"users.paying": 0}),
    ),
]
WITH_GROUP = {"users": {"paying": "plan in ('pro')"}}


@pytest.mark.parametrize(
    ("key", "value", "matching", "other"), DIFF_KEY_CASES, ids=lambda v: str(v)[:24]
)
def test_each_diff_key_matches_and_does_not(
    key: str, value: Any, matching: DiffFacts, other: DiffFacts
) -> None:
    policy = make_policy(r("R", {key: value}, "block"), derived_groups=WITH_GROUP)
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), matching)) == ["R"]
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), other)) == []


def test_every_listed_diff_key_is_covered() -> None:
    from app.services.policy import DIFF_KEYS

    assert {key for key, *_ in DIFF_KEY_CASES} == DIFF_KEYS


def test_rows_affected_sums_every_table_cascades_and_inserts_included() -> None:
    policy = make_policy(r("R", {"rows_affected_gt": 9}, "review"))
    cascade = {"orders": TableFacts(deleted=4, rows_before=9, is_cascade=True)}
    only_main = facts(deleted=5)
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), only_main)) == []
    both = facts(deleted=5, extra=cascade)
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), both)) == []  # 9, not more than 9
    more = facts(deleted=5, inserted=1, extra=cascade)
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), more)) == ["R"]


def test_rows_affected_lte_counts_cascades_too() -> None:
    policy = make_policy(r("R", {"rows_affected_lte": 10}, "allow"))
    cascade = {"orders": TableFacts(deleted=8, rows_before=9, is_cascade=True)}
    assert names(evaluate_pass_two(policy, c(INSERT_SQL), facts(inserted=5))) == ["R"]
    assert names(evaluate_pass_two(policy, c(INSERT_SQL), facts(inserted=5, extra=cascade))) == []


@pytest.mark.parametrize(
    ("deleted", "updated", "before", "gt_05", "gte_1"),
    [
        (500, 0, 1000, False, False),  # exactly half is not more than half
        (501, 0, 1000, True, False),
        (0, 501, 1000, True, False),
        (400, 101, 1000, True, False),  # deleted + updated
        (999, 0, 1000, True, False),
        (1000, 0, 1000, True, True),
        (1, 0, 3, False, False),  # 0.333...
        (2, 0, 3, True, False),  # 0.666...
        (3, 0, 3, True, True),
        (10000000, 0, 20000000, False, False),  # exact where floats could drift
    ],
)
def test_fraction_boundaries_are_exact(
    deleted: int, updated: int, before: int, gt_05: bool, gte_1: bool
) -> None:
    policy = make_policy(
        r("half", {"fraction_of_table_gt": 0.5}, "review"),
        r("all", {"fraction_of_table_gte": 1.0}, "block"),
    )
    result = evaluate_pass_two(policy, c(DELETE_SQL), facts(deleted, updated, before=before))
    assert ("half" in names(result)) is gt_05
    assert ("all" in names(result)) is gte_1


def test_a_fraction_uses_the_largest_changed_table() -> None:
    policy = make_policy(r("half", {"fraction_of_table_gt": 0.5}, "review"))
    cascade = {"orders": TableFacts(deleted=8, rows_before=10, is_cascade=True)}
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), facts(deleted=1, extra=cascade))) == [
        "half"
    ]


def test_inserts_alone_do_not_make_a_fraction() -> None:
    policy = make_policy(r("all", {"fraction_of_table_gte": 1.0}, "block"))
    assert names(evaluate_pass_two(policy, c(INSERT_SQL), facts(inserted=5, before=5))) == []
    assert names(evaluate_pass_two(policy, c(INSERT_SQL), facts(inserted=5, before=0))) == []


def test_a_cascade_table_with_no_changes_does_not_match() -> None:
    policy = make_policy(r("R", {"cascade_tables_any": ["orders"]}, "review"))
    quiet = facts(deleted=1, extra={"orders": TableFacts(rows_before=5, is_cascade=True)})
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), quiet)) == []


def test_a_group_whose_table_was_not_touched_counts_zero() -> None:
    policy = make_policy(
        r("R", {"derived_group_gt": {"users.paying": 0}}, "review"), derived_groups=WITH_GROUP
    )
    other_table = facts(deleted=5, table="orders")
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), other_table)) == []
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), DiffFacts())) == []


def test_every_entry_of_derived_group_gt_must_hold() -> None:
    groups = {"users": {"paying": "plan = 'pro'", "free": "plan = 'free'"}}
    policy = make_policy(
        r("R", {"derived_group_gt": {"users.paying": 0, "users.free": 2}}, "review"),
        derived_groups=groups,
    )
    ok = facts(updated=9, groups={"users.paying": 1, "users.free": 3})
    short = facts(updated=9, groups={"users.paying": 1, "users.free": 2})
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), ok)) == ["R"]
    assert names(evaluate_pass_two(policy, c(DELETE_SQL), short)) == []


# --- Bad facts fail closed ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        facts(deleted=1, before=None),
        facts(inserted=1, before=None),
        facts(deleted=-1),
        facts(updated=-1),
        facts(inserted=-1),
        facts(deleted=1, before=-5),
        facts(deleted=3, before=0),
    ],
)
def test_malformed_facts_raise(bad: DiffFacts) -> None:
    # A policy with no derived groups, so only the table facts can be what raises.
    policy = make_policy(r("R", {"rows_affected_gt": 0}, "review"))
    with pytest.raises(PolicyFactsError):
        evaluate_pass_two(policy, c(DELETE_SQL), bad)


def test_a_negative_group_count_raises() -> None:
    with pytest.raises(PolicyFactsError, match="negative"):
        evaluate_pass_two(SHIPPED, c(DELETE_SQL), facts(updated=1, groups={"users.paying": -1}))


def test_a_changed_table_without_its_group_count_raises() -> None:
    with pytest.raises(PolicyFactsError, match=r"users\.paying"):
        evaluate_pass_two(SHIPPED, c(DELETE_SQL), facts(deleted=1))


def test_an_unchanged_table_needs_no_row_count() -> None:
    quiet = DiffFacts(tables={"users": TableFacts()})
    assert evaluate_pass_two(SHIPPED, c(DELETE_SQL), quiet).decision == Decision.REVIEW


# --- Every shipped rule: one positive and one negative --------------------------------------------

paying = {"users.paying": 1}
no_paying = {"users.paying": 0}

SHIPPED_RULE_CASES: list[tuple[str, str, DiffFacts, str, DiffFacts]] = [
    # (rule, matching sql, its facts, non-matching sql, its facts)
    ("No DDL", "CREATE TABLE t (a int)", NO_DIFF, DELETE_SQL, facts(deleted=1, groups=no_paying)),
    ("Unparseable SQL", "VACUUM", NO_DIFF, DELETE_SQL, facts(deleted=1, groups=no_paying)),
    (
        "No DROP or TRUNCATE",
        "DROP TABLE users",
        NO_DIFF,
        DELETE_SQL,
        facts(deleted=1, groups=no_paying),
    ),
    (
        "No DROP or TRUNCATE",
        "TRUNCATE users",
        NO_DIFF,
        DELETE_SQL,
        facts(deleted=1, groups=no_paying),
    ),
    ("One statement only", "SELECT 1; SELECT 2", NO_DIFF, "SELECT 1", NO_DIFF),
    ("No transaction control", "COMMIT", NO_DIFF, "SELECT 1", NO_DIFF),
    (
        "Hands off Blastproof's schema",
        "SELECT * FROM blastproof.seed_info",
        NO_DIFF,
        "SELECT * FROM users",
        NO_DIFF,
    ),
    (
        "No side-effect functions",
        "SELECT pg_terminate_backend(1)",
        NO_DIFF,
        "SELECT count(*) FROM users",
        NO_DIFF,
    ),
    (
        "Volatile values in WHERE",
        "DELETE FROM users WHERE random() < 0.1",
        NO_DIFF,
        DELETE_SQL,
        NO_DIFF,
    ),
    (
        "Unordered LIMIT",
        "DELETE FROM users WHERE id IN (SELECT id FROM users LIMIT 2)",
        NO_DIFF,
        "DELETE FROM users WHERE id IN (SELECT id FROM users ORDER BY id LIMIT 2)",
        NO_DIFF,
    ),
    ("Delete or update without WHERE", "DELETE FROM users", NO_DIFF, DELETE_SQL, NO_DIFF),
    (
        "Delete or update without WHERE",
        "UPDATE users SET plan = 'x' WHERE true",
        NO_DIFF,
        "UPDATE users SET plan = 'x' WHERE id = 1",
        NO_DIFF,
    ),
    (
        "Every row of a table",
        DELETE_SQL,
        facts(deleted=1000, groups=no_paying),
        DELETE_SQL,
        facts(deleted=999, groups=no_paying),
    ),
    (
        "Every row of a table",
        "UPDATE users SET plan = 'x' WHERE id > 0",
        facts(updated=1000, groups=no_paying),
        INSERT_SQL,
        facts(inserted=1000, before=1000, table="customer_notes"),
    ),
    (
        "Most of a table",
        DELETE_SQL,
        facts(deleted=600, groups=no_paying),
        DELETE_SQL,
        facts(deleted=500, groups=no_paying),
    ),
    (
        "Large change",
        DELETE_SQL,
        facts(deleted=1001, before=10**6, groups=no_paying),
        DELETE_SQL,
        facts(deleted=1000, before=10**6, groups=no_paying),
    ),
    (
        "Touches paying customers",
        DELETE_SQL,
        facts(deleted=1, groups=paying),
        DELETE_SQL,
        facts(deleted=1, groups=no_paying),
    ),
    (
        "Fast-lane note",
        INSERT_SQL,
        facts(inserted=1, table="customer_notes"),
        "INSERT INTO orders (id) VALUES (1)",
        facts(inserted=1, table="orders"),
    ),
    (
        "Small insert",
        INSERT_SQL,
        facts(inserted=10, table="customer_notes"),
        INSERT_SQL,
        facts(inserted=11, table="customer_notes"),
    ),
    (
        "Small insert",
        "INSERT INTO orders (id) VALUES (1)",
        facts(inserted=1, table="orders"),
        DELETE_SQL,
        facts(deleted=1, groups=no_paying),
    ),
]


@pytest.mark.parametrize(
    ("rule", "yes_sql", "yes_facts", "no_sql", "no_facts"),
    SHIPPED_RULE_CASES,
    ids=[f"{case[0]}-{i}" for i, case in enumerate(SHIPPED_RULE_CASES)],
)
def test_each_shipped_rule_matches_and_does_not(
    rule: str, yes_sql: str, yes_facts: DiffFacts, no_sql: str, no_facts: DiffFacts
) -> None:
    assert rule in names(evaluate_pass_two(SHIPPED, c(yes_sql), yes_facts)), rule
    assert rule not in names(evaluate_pass_two(SHIPPED, c(no_sql), no_facts)), rule


def test_every_shipped_rule_has_a_case() -> None:
    assert {case[0] for case in SHIPPED_RULE_CASES} == {rule.name for rule in SHIPPED.rules}


# --- Pass 1: the skip -----------------------------------------------------------------------------


def test_diff_rules_are_skipped_in_pass_one_not_treated_as_false() -> None:
    result = evaluate_pass_one(SHIPPED, c(DELETE_SQL))
    assert set(result.skipped) == {
        "Every row of a table",
        "Most of a table",
        "Large change",
        "Touches paying customers",
        "Small insert",
    }
    assert not set(result.skipped) & set(names(result))
    assert result.route == Route.PREVIEW


def test_a_mixed_block_rule_does_not_block_in_pass_one_but_does_in_pass_two() -> None:
    policy = make_policy(
        r("Mixed", {"statement_in": ["delete"], "rows_affected_gt": 5}, "block"),
    )
    one = evaluate_pass_one(policy, c(DELETE_SQL))
    assert one.route == Route.PREVIEW
    assert one.skipped == ("Mixed",)
    assert one.matched == ()
    two = evaluate_pass_two(policy, c(DELETE_SQL), facts(deleted=6))
    assert two.decision == Decision.BLOCK
    assert evaluate_pass_two(policy, c(DELETE_SQL), facts(deleted=5)).decision == Decision.REVIEW


def test_a_skipped_allow_rule_never_allows_in_pass_one() -> None:
    result = evaluate_pass_one(SHIPPED, c("INSERT INTO orders (id) VALUES (1)"))
    assert "Small insert" in result.skipped
    assert result.route == Route.PREVIEW
    assert result.matched == ()


def test_a_pass_one_block_wins_and_lists_every_match() -> None:
    result = evaluate_pass_one(SHIPPED, c("DELETE FROM blastproof.applied_actions"))
    assert result.route == Route.BLOCK
    assert set(names(result)) == {"Hands off Blastproof's schema", "Delete or update without WHERE"}
    assert result.reason == "The blastproof schema belongs to the gateway."


# --- Fast lane ------------------------------------------------------------------------------------


def test_an_eligible_note_insert_takes_the_fast_lane() -> None:
    result = evaluate_pass_one(SHIPPED, c(INSERT_SQL))
    assert result.route == Route.FAST_LANE
    assert names(result) == ["Fast-lane note"]
    assert result.reason == "Small insert into a notes table."


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "INSERT INTO customer_notes (customer_id, body) VALUES (1, upper('hi'))",
        "INSERT INTO customer_notes (customer_id, body) SELECT 1, 'x'",
        "INSERT INTO customer_notes (customer_id, body) VALUES (1, 'a') RETURNING id",
        "INSERT INTO customer_notes (customer_id, body) VALUES (1, 'a') ON CONFLICT DO NOTHING",
    ],
)
def test_other_inserts_are_previewed_not_fast_laned(sql: str) -> None:
    assert evaluate_pass_one(SHIPPED, c(sql)).route == Route.PREVIEW


def test_an_allow_rule_without_fast_lane_never_opens_the_fast_lane() -> None:
    policy = make_policy(r("Notes", {"tables_any": ["customer_notes"]}, "allow"))
    assert c(INSERT_SQL).fast_lane_eligible
    assert evaluate_pass_one(policy, c(INSERT_SQL)).route == Route.PREVIEW


def test_a_matched_review_rule_closes_the_fast_lane() -> None:
    policy = make_policy(
        r("Fast", {"fast_lane": True, "tables_any": ["customer_notes"]}, "allow"),
        r("Notes need a look", {"tables_any": ["customer_notes"]}, "review"),
    )
    assert evaluate_pass_one(policy, c(INSERT_SQL)).route == Route.PREVIEW


def test_a_block_beats_the_fast_lane() -> None:
    policy = make_policy(
        r("Fast", {"fast_lane": True, "tables_any": ["customer_notes"]}, "allow"),
        r("No notes", {"tables_any": ["customer_notes"]}, "block"),
    )
    assert evaluate_pass_one(policy, c(INSERT_SQL)).route == Route.BLOCK


def test_the_fast_lane_needs_the_table_to_be_listed() -> None:
    policy = make_policy(r("Fast", {"fast_lane": True, "tables_any": ["other"]}, "allow"))
    assert evaluate_pass_one(policy, c(INSERT_SQL)).route == Route.PREVIEW


# --- Pass 2: strictest wins -----------------------------------------------------------------------

OUTCOMES = ["allow", "review", "block"]
STRICTNESS = {"allow": 0, "review": 1, "block": 2}


@pytest.mark.parametrize(
    "combo",
    [combo for size in (1, 2, 3) for combo in itertools.product(OUTCOMES, repeat=size)],
)
def test_the_strictest_matching_outcome_wins_in_any_order(combo: tuple[str, ...]) -> None:
    rules = [r(f"rule{i}", {"statement_in": ["delete"]}, then) for i, then in enumerate(combo)]
    expected = max(combo, key=STRICTNESS.__getitem__)
    for ordered in itertools.permutations(rules):
        result = evaluate_pass_two(make_policy(*ordered), c(DELETE_SQL), NO_DIFF)
        assert result.decision == Decision(expected)
        assert len(result.matched) == len(combo)


def test_the_reason_is_the_first_matching_rule_with_the_winning_outcome() -> None:
    policy = make_policy(
        r("a", {"statement_in": ["delete"]}, "allow", "allow reason"),
        r("b", {"statement_in": ["delete"]}, "review", "first review"),
        r("c", {"statement_in": ["delete"]}, "review", "second review"),
        r("d", {"kind_in": ["read"]}, "block", "never matches"),
    )
    result = evaluate_pass_two(policy, c(DELETE_SQL), NO_DIFF)
    assert result.decision == Decision.REVIEW
    assert result.reason == "first review"
    assert names(result) == ["a", "b", "c"]


def test_no_matching_rule_means_review() -> None:
    policy = make_policy(r("Never", {"kind_in": ["ddl"]}, "block"))
    result = evaluate_pass_two(policy, c(DELETE_SQL), facts(deleted=1))
    assert result.decision == Decision.REVIEW
    assert result.reason == NO_RULE_REASON
    assert result.matched == ()


def test_an_empty_policy_reviews_everything() -> None:
    result = evaluate_pass_two(make_policy(), c(INSERT_SQL), facts(inserted=1))
    assert result.decision == Decision.REVIEW


def test_an_allow_needs_a_positive_allow_match() -> None:
    # Only a review-free, block-free write with no allow rule still goes to review.
    result = evaluate_pass_two(SHIPPED, c(DELETE_SQL), facts(deleted=1, groups=no_paying))
    assert result.decision == Decision.REVIEW
    assert result.matched == ()


def test_a_small_clean_insert_is_allowed() -> None:
    result = evaluate_pass_two(
        SHIPPED, c("INSERT INTO orders (id) VALUES (1)"), facts(inserted=1, table="orders")
    )
    assert result.decision == Decision.ALLOW
    assert result.reason == "Small insert."


def test_a_stricter_match_beats_an_allow() -> None:
    result = evaluate_pass_two(
        SHIPPED,
        c(INSERT_SQL),
        facts(inserted=2000, table="customer_notes", before=5),
    )
    assert result.decision == Decision.REVIEW
    assert "Large change" in names(result)


def test_the_replit_shape_is_review_not_block() -> None:
    sql = "DELETE FROM users WHERE last_login_at < now() - interval '1 year'"
    result = evaluate_pass_two(
        SHIPPED, c(sql), facts(deleted=48000, before=48213, groups={"users.paying": 12})
    )
    assert result.decision == Decision.REVIEW
    assert set(names(result)) == {"Most of a table", "Large change", "Touches paying customers"}


# --- Known limits, pinned so a change is deliberate (D-131) ---------------------------------------


def test_a_merge_is_held_to_the_fraction_rules() -> None:
    sql = "MERGE INTO users u USING (SELECT 1 AS id) s ON u.id = s.id WHEN MATCHED THEN DELETE"
    result = c(sql)
    assert result.statement_types == ("merge",)
    everything = evaluate_pass_two(SHIPPED, result, facts(deleted=1000, groups=no_paying))
    assert everything.decision == Decision.BLOCK
    assert "Every row of a table" in names(everything)
    most = evaluate_pass_two(SHIPPED, result, facts(deleted=600, groups=no_paying))
    assert most.decision == Decision.REVIEW
    assert "Most of a table" in names(most)


def test_known_limit_cte_delete_feeding_an_insert_matches_small_insert() -> None:
    sql = (
        "WITH d AS (DELETE FROM users WHERE id = 1 RETURNING id) "
        "INSERT INTO customer_notes (customer_id, body) SELECT id, 'x' FROM d"
    )
    result = c(sql)
    assert set(result.statement_types) >= {"delete", "insert"}
    two = evaluate_pass_two(
        SHIPPED,
        result,
        facts(
            deleted=1,
            groups=no_paying,
            extra={"customer_notes": TableFacts(inserted=1, rows_before=5)},
        ),
    )
    assert two.decision == Decision.ALLOW
    assert names(two) == ["Small insert"]


def test_known_limit_removing_a_block_rule_removes_the_block() -> None:
    policy = make_policy(r("Only", {"kind_in": ["ddl"]}, "block"))
    assert evaluate_pass_one(policy, c("VACUUM")).route == Route.PREVIEW


# --- BE-28 and OBS-07 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql", ["SELECT '" + "x" * 21000 + "'", "this is not sql at all (((", "VACUUM", ""]
)
def test_unparseable_input_is_blocked_in_pass_one(sql: str) -> None:
    result = evaluate_pass_one(SHIPPED, c(sql))
    assert result.route == Route.BLOCK
    assert "Unparseable SQL" in names(result)
    assert "does not support" in result.reason


@pytest.mark.parametrize("sql", [DELETE_SQL, INSERT_SQL, "SELECT 1", "VACUUM", "DROP TABLE users"])
def test_every_decision_has_a_reason(sql: str) -> None:
    one = evaluate_pass_one(SHIPPED, c(sql))
    assert one.reason.strip()
    two = evaluate_pass_two(SHIPPED, c(sql), facts(deleted=1, groups=no_paying))
    assert two.reason.strip()
    assert all(m.reason.strip() for m in two.matched)


def test_the_key_lists_are_exactly_the_condition_fields() -> None:
    from app.services.policy import DIFF_KEYS, SQL_KEYS, Conditions

    assert set(Conditions.model_fields) == SQL_KEYS | DIFF_KEYS
    assert not SQL_KEYS & DIFF_KEYS
