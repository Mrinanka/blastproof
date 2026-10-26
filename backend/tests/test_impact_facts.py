"""Step 12 (BE-64, ARCH-06): the impact facts are computed in code from the shadow run's diff."""

from decimal import Decimal

import pytest

from app.services.impact import OTHER_LABEL, build_facts, percent_of, top_labels
from app.services.shadow import Diff, TableDiff
from tests.impact_helpers import (
    POLICY,
    REPLIT_SQL,
    SEED_ROWS,
    classified,
    replit_diff,
    replit_scenario,
)


def test_the_replit_facts_match_incidents_yaml() -> None:
    expected = replit_scenario().expected_impact
    assert expected is not None
    facts = build_facts(replit_diff(), classified(REPLIT_SQL), POLICY)

    assert facts.statement_type == "delete"
    assert facts.target_tables == ("users",)
    by_table = {t.table: t for t in facts.tables}
    assert set(by_table) == set(expected.table_changes)
    for name, change in expected.table_changes.items():
        t = by_table[name]
        assert (t.deleted, t.updated, t.inserted) == (
            change.deleted,
            change.updated,
            change.inserted,
        )
        assert t.rows_before == SEED_ROWS[name]
        assert t.is_cascade == (name != "users")
    assert facts.breakdowns == {"users.plan": {"free": 48_201, "pro": 9, "enterprise": 3}}
    assert [(g.name, g.count, g.definition) for g in facts.groups] == [
        ("users.paying", 12, "plan in ('pro', 'enterprise')")
    ]
    assert facts.total_deleted == 241_295
    assert facts.total_updated == 4_382
    assert facts.total_inserted == 0
    assert facts.total_rows_affected == 245_677
    assert facts.tables_changed == 8
    assert facts.cascade_tables == 7
    assert by_table["users"].percent_of_table == Decimal("96.4")


def test_cascades_carry_their_path_and_action() -> None:
    facts = build_facts(replit_diff(), classified(REPLIT_SQL), POLICY)
    found = {(c.table, c.via, c.on_delete): c.count for c in facts.cascades}
    assert found[("order_items", ("orders",), "cascade")] == 121_364
    assert found[("invoices", (), "set_null")] == 4_382
    assert len(found) == 7


@pytest.mark.parametrize(
    ("changed", "rows", "expected"),
    [
        (1, 16, Decimal("6.3")),  # 6.25: half up, not to even
        (3, 16, Decimal("18.8")),  # 18.75
        (1, 8, Decimal("12.5")),
        (1, 3, Decimal("33.3")),
        (2, 3, Decimal("66.7")),
        (1, 200_000, Decimal("0.0")),
        (5, 5, Decimal("100.0")),
        (0, 10, None),
        (3, 0, None),
    ],
)
def test_percentages_round_half_up_at_one_decimal(
    changed: int, rows: int, expected: Decimal | None
) -> None:
    assert percent_of(changed, rows) == expected


def table_diff(**tables: TableDiff) -> Diff:
    return Diff(tables=dict(tables))


def test_an_insert_only_table_has_no_percentage() -> None:
    diff = table_diff(customer_notes=TableDiff("customer_notes", inserted=3, rows_before=10))
    facts = build_facts(
        diff, classified("INSERT INTO customer_notes (user_id, body) VALUES (1, 'a')"), POLICY
    )
    [t] = facts.tables
    assert t.percent_of_table is None
    assert facts.total_inserted == 3 and facts.total_rows_affected == 3


def test_an_empty_table_has_no_percentage() -> None:
    diff = table_diff(feature_flags=TableDiff("feature_flags", inserted=1, rows_before=0))
    facts = build_facts(diff, classified("INSERT INTO feature_flags (name) VALUES ('x')"), POLICY)
    assert facts.tables[0].percent_of_table is None


def test_unchanged_tables_are_dropped_and_order_is_targets_then_name() -> None:
    diff = table_diff(
        zeta=TableDiff("zeta", deleted=1, rows_before=5, is_cascade=True),
        users=TableDiff("users", deleted=2, rows_before=10),
        alpha=TableDiff("alpha", updated=1, rows_before=5, is_cascade=True),
        products=TableDiff("products", rows_before=40),
    )
    facts = build_facts(diff, classified("DELETE FROM users WHERE id < 3"), POLICY)
    assert [t.table for t in facts.tables] == ["users", "alpha", "zeta"]
    assert facts.tables_changed == 3 and facts.cascade_tables == 2


def test_a_breakdown_keeps_the_top_20_and_sums_the_rest_exactly() -> None:
    counts = {f"label{i:02d}": 100 - i for i in range(25)}
    counts["NULL"] = 7
    top = top_labels(counts)
    assert len(top) == 21
    assert list(top)[:3] == ["label00", "label01", "label02"]
    assert top[OTHER_LABEL] == sum(counts.values()) - sum(v for k, v in top.items() if k != "other")
    assert sum(top.values()) == sum(counts.values())


def test_breakdown_ties_are_ordered_by_label() -> None:
    assert list(top_labels({"b": 1, "a": 1, "c": 2})) == ["c", "a", "b"]


def test_a_null_label_is_kept() -> None:
    assert top_labels({"NULL": 3, "pro": 1}) == {"NULL": 3, "pro": 1}


def test_a_real_label_named_other_is_not_overwritten() -> None:
    counts = {"other": 1_000} | {f"l{i:02d}": 10 for i in range(25)}
    top = top_labels(counts)
    assert top["other"] == 1_000
    assert top["other (rest)"] == 60  # 26 labels: "other" and 19 more kept, 6 of 10 summed


def test_the_facts_use_the_summed_breakdown() -> None:
    labels = {f"plan{i:02d}": 30 - i for i in range(25)}
    diff = Diff(
        tables={"users": TableDiff("users", deleted=sum(labels.values()), rows_before=50_000)},
        breakdowns={"users.plan": labels},
        derived_groups={"users.paying": 0},
    )
    facts = build_facts(diff, classified("DELETE FROM users WHERE id < 100"), POLICY)
    assert len(facts.breakdowns["users.plan"]) == 21
    assert sum(facts.breakdowns["users.plan"].values()) == sum(labels.values())


def test_several_target_tables_from_a_cte() -> None:
    sql = (
        "WITH gone AS (DELETE FROM orders WHERE id < 10 RETURNING user_id)"
        " UPDATE users SET plan = 'free' WHERE id IN (SELECT user_id FROM gone)"
    )
    classification = classified(sql)
    assert set(classification.target_tables) == {"orders", "users"}
    diff = table_diff(
        order_items=TableDiff("order_items", deleted=20, rows_before=100, is_cascade=True),
        orders=TableDiff("orders", deleted=9, rows_before=50),
        users=TableDiff("users", updated=4, rows_before=40),
    )
    facts = build_facts(diff, classification, POLICY)
    assert facts.target_tables == ("orders", "users")
    assert [t.table for t in facts.tables] == ["orders", "users", "order_items"]
    assert facts.cascade_tables == 1


def test_update_from_targets_only_the_updated_table() -> None:
    classification = classified(
        "UPDATE invoices SET status = 'void' FROM users"
        " WHERE invoices.user_id = users.id AND users.plan = 'free'"
    )
    assert classification.target_tables == ("invoices",)
    diff = table_diff(invoices=TableDiff("invoices", updated=5, rows_before=10))
    facts = build_facts(diff, classification, POLICY)
    assert facts.target_tables == ("invoices",)
    assert facts.tables[0].percent_of_table == Decimal("50.0")


def test_groups_on_a_non_target_table_are_reported() -> None:
    diff = Diff(
        tables={
            "orders": TableDiff("orders", deleted=3, rows_before=10),
            "users": TableDiff("users", updated=2, rows_before=10, is_cascade=True),
        },
        derived_groups={"users.paying": 2},
    )
    facts = build_facts(diff, classified("DELETE FROM orders WHERE id < 4"), POLICY)
    assert [(g.name, g.count) for g in facts.groups] == [("users.paying", 2)]


def test_a_group_the_policy_does_not_define_is_a_bug_that_raises() -> None:
    diff = Diff(
        tables={"users": TableDiff("users", deleted=1, rows_before=10)},
        derived_groups={"users.vip": 1},
    )
    with pytest.raises(KeyError):
        build_facts(diff, classified("DELETE FROM users WHERE id = 1"), POLICY)


def test_the_prompt_json_holds_every_fact_and_no_rows() -> None:
    facts = build_facts(replit_diff(), classified(REPLIT_SQL), POLICY)
    data = facts.to_prompt_json()
    assert data["totals"]["rows_affected"] == 245_677
    assert data["tables"][0] == {
        "table": "users",
        "deleted": 48_213,
        "updated": 0,
        "inserted": 0,
        "rows_before": 50_000,
        "is_cascade": False,
        "percent_of_table": "96.4",
    }
    assert data["derived_groups"] == [
        {"name": "users.paying", "count": 12, "definition": "plan in ('pro', 'enterprise')"}
    ]
    assert len(data["cascades"]) == 7
