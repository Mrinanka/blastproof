"""DW-B10: the Replit delete, measured on a real copy (FakeNeonClient, never Neon).

Frozen clock, so `now() - interval '6 months'` pins to the instant the expected counts hold at.
"""

import asyncpg
import pytest

from app.core.clock import FrozenClock
from app.db.enums import Decision
from app.services.classifier import classify
from app.services.policy import evaluate_pass_two
from app.services.shadow import Diff, ShadowResult
from evals.schema import ExpectedImpact
from tests.conftest import (
    COMPANY_READER_URL,
    REPLIT_SCENARIO_ID,
    fake_neon_client,
    leftover_shadow_databases,
    replit_impact,
    shadow_runner,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

TOTAL_CHANGED_ROWS = 245_677  # 241,295 deleted rows plus 4,382 invoices set to NULL


async def preview(frozen_clock: FrozenClock, sql: str | None = None) -> ShadowResult:
    from uuid import uuid4

    from evals.schema import SqlScenario, load_scenario_file
    from tests.conftest import SCENARIOS_DIR

    if sql is None:
        scenarios = load_scenario_file(SCENARIOS_DIR / "incidents.yaml").scenarios
        sql = next(
            s.sql for s in scenarios if isinstance(s, SqlScenario) and s.id == REPLIT_SCENARIO_ID
        )
    classification = classify(sql, now=frozen_clock.now())
    runner = shadow_runner({"local": fake_neon_client(frozen_clock)})
    return await runner.run(action_id=uuid4(), profile="local", classification=classification)


def table_changes(diff: Diff) -> dict[str, dict[str, int]]:
    return {
        name: {
            k: v
            for k, v in (("deleted", t.deleted), ("updated", t.updated), ("inserted", t.inserted))
            if v
        }
        for name, t in diff.tables.items()
        if t.changed
    }


async def test_the_replit_delete_is_measured_exactly(frozen_clock: FrozenClock) -> None:
    result = await preview(frozen_clock)
    diff, expected = result.diff, replit_impact()
    assert table_changes(diff) == {
        t: change.model_dump(exclude_defaults=True) for t, change in expected.table_changes.items()
    }
    assert {t for t, d in diff.tables.items() if d.is_cascade} == set(expected.table_changes) - {
        "users"
    }
    assert diff.breakdowns == expected.breakdowns
    assert diff.derived_groups == expected.derived_groups
    assert len(diff.changes) == TOTAL_CHANGED_ROWS
    assert (
        ExpectedImpact.model_validate(
            {k: v for k, v in diff.impact_json().items() if k in ExpectedImpact.model_fields}
        )
        == expected
    )


async def test_cascade_expectations_follow_the_foreign_keys(frozen_clock: FrozenClock) -> None:
    diff = (await preview(frozen_clock)).diff
    found = {
        (p.table, tuple(e.child for e in p.edges), p.on_delete): p.count
        for p in diff.cascade_expectations
    }
    assert found == {
        ("orders", ("orders",), "cascade"): 48_500,
        ("order_items", ("orders", "order_items"), "cascade"): 121_364,
        ("subscriptions", ("subscriptions",), "cascade"): 1_476,
        ("payments", ("payments",), "cascade"): 4_382,
        ("support_tickets", ("support_tickets",), "cascade"): 11_647,
        ("customer_notes", ("customer_notes",), "cascade"): 5_713,
        ("invoices", ("invoices",), "set_null"): 4_382,
    }


async def test_pass_two_on_the_measured_facts_is_review(frozen_clock: FrozenClock) -> None:
    """BE-45: measured, not predicted, the Replit delete goes to a person."""
    from app.config import DEFAULT_POLICY_FILE
    from app.services.policy import load_policy

    result = await preview(frozen_clock)
    classification = classify(
        "DELETE FROM users WHERE last_login_at < now() - interval '6 months'",
        now=frozen_clock.now(),
    )
    decision = evaluate_pass_two(
        load_policy(DEFAULT_POLICY_FILE), classification, result.diff.facts()
    )
    assert decision.decision == Decision.REVIEW
    assert {m.name for m in decision.matched} == {
        "Most of a table",
        "Large change",
        "Touches paying customers",
    }


async def test_samples_are_masked_and_capped(frozen_clock: FrozenClock) -> None:
    diff = (await preview(frozen_clock)).diff
    users = [s for s in diff.samples if s.table == "users"]
    assert len(users) == 50 and {s.side for s in users} == {"before"}
    assert all(
        s.values["email"] == "[masked]" and s.values["full_name"] == "[masked]" for s in users
    )
    assert diff.samples[0].table == "users"  # target tables first
    invoices = {s.side for s in diff.samples if s.table == "invoices"}
    assert invoices == {"before", "after"}
    dumped = str(diff.impact_json())
    assert "@" not in dumped


async def count_users() -> int:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        return await conn.fetchval("SELECT count(*) FROM users")
    finally:
        await conn.close()


async def test_company_main_is_unchanged_and_no_copy_is_left(frozen_clock: FrozenClock) -> None:
    before = await count_users()
    await preview(frozen_clock)
    assert await count_users() == before
    assert await leftover_shadow_databases() == []


async def test_two_previews_in_a_row_are_identical(frozen_clock: FrozenClock) -> None:
    first, second = await preview(frozen_clock), await preview(frozen_clock)
    assert first.diff.tables == second.diff.tables
    assert first.diff.breakdowns == second.diff.breakdowns
    assert first.diff.derived_groups == second.diff.derived_groups
    key = lambda c: (c.table, str(sorted(c.primary_key.items())))  # noqa: E731
    assert sorted(first.diff.changes, key=key) == sorted(second.diff.changes, key=key)
