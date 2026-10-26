"""Step 12: the template summary is built from the facts alone and always passes the guardrail.

Checked with an empty intent and SQL, so it passes on the computed facts and thresholds only.
"""

import pytest

from app.services.impact import (
    ImpactFacts,
    allowed_numbers,
    build_facts,
    shape_ok,
    template_report,
    unknown_numbers,
)
from app.services.shadow import CascadePath, Diff, TableDiff
from evals.schema import SqlScenario
from tests.impact_helpers import (
    POLICY,
    REPLIT_SQL,
    classified,
    diff_from,
    fk,
    replit_diff,
    scenarios_with_impact,
)


def check(facts: ImpactFacts) -> None:
    report = template_report(facts)
    allowed = allowed_numbers(facts, intent="", sql_display="", policy=POLICY)
    assert unknown_numbers(report, allowed) == 0, report
    assert report.headline.strip()


@pytest.mark.parametrize("scenario", scenarios_with_impact(), ids=lambda s: s.id)
def test_the_template_passes_for_every_measured_scenario(scenario: SqlScenario) -> None:
    assert scenario.expected_impact is not None
    classification = classified(scenario.sql)
    diff = diff_from(scenario.expected_impact, classification.target_tables)
    check(build_facts(diff, classification, POLICY))


def test_the_replit_template_reads_like_the_plan() -> None:
    report = template_report(build_facts(replit_diff(), classified(REPLIT_SQL), POLICY))
    assert report.headline == (
        "Deletes 48,213 rows from users (96.4% of the table), including 12 in users.paying."
    )
    assert "In total 245,677 rows change across 8 tables: 241,295 deleted, 4,382 updated." in (
        report.key_points
    )
    assert "By users.plan: free 48,201, pro 9, enterprise 3." in report.key_points
    assert "invoices: 4,382 updated through foreign keys (SET NULL)." in report.key_points
    assert "order_items: 121,364 deleted through foreign keys via orders." in report.key_points
    assert report.concerns == [
        "12 affected rows are in users.paying (plan in ('pro', 'enterprise')).",
        "It changes 96.4% of users, most of the table.",
        "The change reaches 7 other tables through foreign keys: customer_notes, invoices,"
        " order_items, orders, payments, subscriptions, support_tickets.",
    ]
    check(build_facts(replit_diff(), classified(REPLIT_SQL), POLICY))


def test_insert_only() -> None:
    diff = Diff(tables={"customer_notes": TableDiff("customer_notes", inserted=1, rows_before=9)})
    facts = build_facts(
        diff, classified("INSERT INTO customer_notes (user_id, body) VALUES (1, 'x')"), POLICY
    )
    report = template_report(facts)
    assert report.headline == "Inserts 1 row into customer_notes."
    assert report.concerns == []
    check(facts)


def test_set_null_only() -> None:
    diff = Diff(
        tables={
            "organizations": TableDiff("organizations", deleted=2, rows_before=2_000),
            "users": TableDiff("users", updated=31, rows_before=50_000, is_cascade=True),
        },
        derived_groups={"users.paying": 1},
        cascade_expectations=(
            CascadePath(
                "organizations", (fk("users", "organizations", "set_null"),), "set_null", 31
            ),
        ),
    )
    facts = build_facts(diff, classified("DELETE FROM organizations WHERE id < 3"), POLICY)
    report = template_report(facts)
    assert report.headline == (
        "Deletes 2 rows from organizations (0.1% of the table), including 1 in users.paying."
    )
    assert "users: 31 updated through foreign keys (SET NULL)." in report.key_points
    check(facts)


def test_no_groups_and_an_update() -> None:
    diff = Diff(tables={"products": TableDiff("products", updated=40, rows_before=40)})
    facts = build_facts(diff, classified("UPDATE products SET price = price WHERE id > 0"), POLICY)
    report = template_report(facts)
    assert report.headline == "Updates 40 rows in products (100.0% of the table)."
    assert report.concerns == ["It changes 100.0% of products, most of the table."]
    check(facts)


def test_an_empty_diff() -> None:
    facts = build_facts(
        Diff(tables={"users": TableDiff("users", rows_before=50_000)}),
        classified("DELETE FROM users WHERE id < 0"),
        POLICY,
    )
    report = template_report(facts)
    assert (report.headline, report.key_points, report.concerns) == ("Changes no rows.", [], [])
    check(facts)


def test_the_template_shape_is_valid_for_a_small_change() -> None:
    diff = Diff(tables={"users": TableDiff("users", deleted=1, rows_before=50_000)})
    assert shape_ok(template_report(build_facts(diff, classified(REPLIT_SQL), POLICY)))
