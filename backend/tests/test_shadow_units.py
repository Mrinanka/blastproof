"""Step 10: the pure parts of the shadow executor (no database)."""

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.services.company_schema import ForeignKey, pk_json_sql, quote_ident, quote_literal
from app.services.policy import DiffFacts, load_policy
from app.services.shadow import (
    CascadePath,
    Diff,
    RowChange,
    SampleRow,
    ShadowRunner,
    TableDiff,
    fk_paths,
    mask_row,
)

POLICY = load_policy(DEFAULT_POLICY_FILE)


def fk(
    child: str,
    parent: str,
    on_delete: str = "cascade",
    cols: tuple[str, ...] = ("user_id",),
    parent_cols: tuple[str, ...] = ("id",),
) -> ForeignKey:
    return ForeignKey(child, cols, parent, parent_cols, on_delete)


DEMO = (
    fk("subscriptions", "users"),
    fk("orders", "users"),
    fk("order_items", "orders", cols=("order_id",)),
    fk("invoices", "users", "set_null"),
    fk("users", "organizations", "set_null", cols=("organization_id",)),
)


def shape(paths: list[tuple[ForeignKey, ...]]) -> list[tuple[str, ...]]:
    return [tuple(e.child for e in p) for p in paths]


def test_fk_paths_for_the_demo_shape() -> None:
    assert shape(fk_paths(DEMO, "users")) == [
        ("invoices",),
        ("orders",),
        ("orders", "order_items"),
        ("subscriptions",),
    ]


def test_set_null_stops_the_chain() -> None:
    keys = (fk("invoices", "users", "set_null"), fk("lines", "invoices", cols=("invoice_id",)))
    assert shape(fk_paths(keys, "users")) == [("invoices",)]


def test_no_action_and_restrict_are_skipped() -> None:
    keys = (
        fk("a", "users", "no_action"),
        fk("b", "users", "restrict"),
        fk("c", "users", "set_default"),
    )
    assert shape(fk_paths(keys, "users")) == [("c",)]


def test_depth_is_capped_at_three() -> None:
    chain = (fk("b", "a"), fk("c", "b"), fk("d", "c"), fk("e", "d"))
    assert shape(fk_paths(chain, "a")) == [("b",), ("b", "c"), ("b", "c", "d")]
    assert shape(fk_paths(chain, "a", max_depth=1)) == [("b",)]


def test_cycles_do_not_loop() -> None:
    keys = (fk("b", "a"), fk("a", "b"), fk("a", "a"))
    assert shape(fk_paths(keys, "a")) == [("b",)]


def test_a_composite_key_is_one_edge() -> None:
    keys = (fk("child", "parent", cols=("p1", "p2"), parent_cols=("a", "b")),)
    (path,) = fk_paths(keys, "parent")
    assert path[0].child_columns == ("p1", "p2") and path[0].parent_columns == ("a", "b")


def test_mask_row_masks_configured_columns() -> None:
    row = {"id": 1, "email": "ann@example.com", "full_name": "Ann Lee", "plan": "pro"}
    assert mask_row(POLICY, "users", row) == {
        "id": 1,
        "email": "[masked]",
        "full_name": "[masked]",
        "plan": "pro",
    }


def test_mask_row_masks_by_name_heuristic() -> None:
    row = {"billing_address": "1 Main St", "api_token": "abc", "subject": "hello"}
    masked = mask_row(POLICY, "support_tickets", row)
    assert masked == {"billing_address": "[masked]", "api_token": "[masked]", "subject": "hello"}


def test_mask_row_hides_emails_and_phones_inside_text() -> None:
    masked = mask_row(POLICY, "customer_notes", {"body": "call +1 415 555 0132 or ann@example.com"})
    assert masked == {"body": "call [phone] or [email]"}


def test_mask_row_leaves_numbers_booleans_and_nulls() -> None:
    row = {"n": 5, "ok": True, "gone": None, "phone": None}
    assert mask_row(POLICY, "users", row) == row


def diff() -> Diff:
    return Diff(
        tables={
            "users": TableDiff("users", deleted=3, rows_before=10),
            "orders": TableDiff("orders", deleted=5, updated=1, rows_before=20, is_cascade=True),
            "products": TableDiff("products", rows_before=7),
        },
        breakdowns={"users.plan": {"free": 3}},
        derived_groups={"users.paying": 0},
        cascade_expectations=(CascadePath("users", (fk("orders", "users"),), "cascade", 5),),
        samples=(SampleRow("users", "deleted", "before", {"id": 1}),),
        changes=(RowChange("users", {"id": 1}, "abc123", "deleted"),),
    )


def test_facts_match_what_the_policy_reads() -> None:
    facts = diff().facts()
    assert isinstance(facts, DiffFacts)
    assert facts.tables["users"].deleted == 3 and facts.tables["users"].rows_before == 10
    assert facts.tables["orders"].is_cascade and facts.tables["orders"].changed == 6
    assert facts.tables["products"].rows_before == 7 and not facts.tables["products"].changed
    assert facts.derived_groups == {"users.paying": 0}


def test_impact_json_has_counts_and_no_per_row_keys() -> None:
    impact = diff().impact_json()
    assert impact["table_changes"] == {
        "users": {"deleted": 3, "updated": 0, "inserted": 0},
        "orders": {"deleted": 5, "updated": 1, "inserted": 0},
    }
    assert impact["breakdowns"] == {"users.plan": {"free": 3}}
    assert impact["cascade_tables"] == ["orders"]
    text = str(impact)
    assert "abc123" not in text and "primary_key" not in text and "before_hash" not in text


def test_cascade_json_says_where_each_path_goes() -> None:
    assert diff().cascade_json() == [
        {"target": "users", "table": "orders", "via": [], "on_delete": "cascade", "count": 5}
    ]


def test_pk_json_and_quoting() -> None:
    assert pk_json_sql("t", ["id"]) == 'jsonb_build_object(\'id\', "t"."id")'
    assert pk_json_sql("a", ["x", "y"]) == 'jsonb_build_object(\'x\', "a"."x", \'y\', "a"."y")'
    assert quote_ident('we"ird') == '"we""ird"'
    assert quote_literal("it's") == "'it''s'"


def test_the_runner_has_no_timeout_default_of_its_own() -> None:
    """Gate audit: SHADOW_TIMEOUT_SECONDS (D-143) is the only source, so leaving it out fails."""
    with pytest.raises(TypeError, match="timeout_seconds"):
        ShadowRunner(policy=POLICY, clients={})  # type: ignore[call-arg]
    assert ShadowRunner(policy=POLICY, clients={}, timeout_seconds=60)._timeout == 60
