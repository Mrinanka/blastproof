"""Step 9: pass 2 on the demo scenarios, with facts built from each scenario's measured impact.

`test_seed_counts` proves `expected_impact` is true on the seed, so these are the numbers the
shadow run (step 10) will produce. Row counts per table come from `company_main`.
"""

import asyncio
from collections.abc import Mapping

import asyncpg
import pytest

from app.config import DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.db.enums import Decision
from app.services.classifier import classify
from app.services.policy import (
    DiffFacts,
    PassOneResult,
    PolicyDecision,
    Route,
    TableFacts,
    check_columns,
    evaluate_pass_one,
    evaluate_pass_two,
    load_policy,
)
from evals.schema import ExpectedImpact, SqlScenario, load_scenario_file
from tests.conftest import COMPANY_READER_URL, SCENARIOS_DIR

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

POLICY = load_policy(DEFAULT_POLICY_FILE)

CompanyFacts = tuple[dict[str, int], dict[str, set[str]]]

# The two labeled actions the rules alone get "wrong" on purpose: the judge settles them (step 13).
RULES_ONLY_OUTCOME = {
    "close-one-ticket": Decision.REVIEW,
    "delete-inactive-enterprise-customer": Decision.REVIEW,
}


async def read_company() -> CompanyFacts:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        counts: dict[str, int] = {}
        for row in await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
        ):
            name = row["tablename"]
            counts[name] = await conn.fetchval(f'SELECT count(*) FROM "{name}"')
        columns: dict[str, set[str]] = {}
        for row in await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public'"
        ):
            columns.setdefault(row["table_name"], set()).add(row["column_name"])
        return counts, columns
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def company() -> CompanyFacts:
    return asyncio.run(read_company())


def measured() -> list[SqlScenario]:
    found = []
    for name in ("incidents.yaml", "labeled.yaml"):
        for s in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if isinstance(s, SqlScenario) and s.expected_impact is not None:
                found.append(s)
    return found


def facts_from(
    impact: ExpectedImpact, rows_before: Mapping[str, int], targets: tuple[str, ...]
) -> DiffFacts:
    tables = {
        table: TableFacts(
            deleted=change.deleted,
            updated=change.updated,
            inserted=change.inserted,
            rows_before=rows_before[table],
            is_cascade=table not in targets,
        )
        for table, change in impact.table_changes.items()
    }
    return DiffFacts(tables=tables, derived_groups=dict(impact.derived_groups))


def decide(
    scenario: SqlScenario, frozen_clock: FrozenClock, rows_before: Mapping[str, int]
) -> tuple[PassOneResult, PolicyDecision]:
    """Pass 1 then pass 2, as the graph will run them."""
    assert scenario.expected_impact is not None
    classification = classify(scenario.sql, now=frozen_clock.now())
    one = evaluate_pass_one(POLICY, classification)
    facts = facts_from(scenario.expected_impact, rows_before, classification.target_tables)
    return one, evaluate_pass_two(POLICY, classification, facts)


@pytest.mark.parametrize("scenario", measured(), ids=lambda s: s.id)
def test_pass_two_matches_the_scenario_labels(
    scenario: SqlScenario, frozen_clock: FrozenClock, company: CompanyFacts
) -> None:
    one, two = decide(scenario, frozen_clock, company[0])
    assert one.route != Route.BLOCK, f"{scenario.id} should reach the shadow run"
    if "Fast-lane note" in scenario.expected_rules:
        return  # the fast lane never reaches pass 2
    assert {m.name for m in two.matched} == set(scenario.expected_rules), scenario.id
    expected = RULES_ONLY_OUTCOME.get(scenario.id, Decision(scenario.expected_decision))
    assert two.decision == expected, scenario.id
    assert two.reason.strip()


def test_the_replit_delete_is_review_not_block(
    frozen_clock: FrozenClock, company: CompanyFacts
) -> None:
    """BE-45: the demo's headline write is held for a person, not refused."""
    replit = next(s for s in measured() if s.id == "replit-clean-up-inactive-users")
    _, two = decide(replit, frozen_clock, company[0])
    assert two.decision == Decision.REVIEW
    assert {m.name for m in two.matched} == {
        "Most of a table",
        "Large change",
        "Touches paying customers",
    }


@pytest.mark.parametrize("scenario_id", sorted(RULES_ONLY_OUTCOME))
def test_rules_alone_get_the_judge_cases_wrong_on_purpose(
    scenario_id: str, frozen_clock: FrozenClock, company: CompanyFacts
) -> None:
    """Named so a change to the rules does not quietly fix or break these two."""
    scenario = next((s for s in measured() if s.id == scenario_id), None)
    assert scenario is not None, f"{scenario_id} has no expected_impact to measure"
    _, two = decide(scenario, frozen_clock, company[0])
    assert two.decision == RULES_ONLY_OUTCOME[scenario_id]
    assert two.decision != Decision(scenario.expected_decision)


def test_the_shipped_columns_exist_in_the_company_database(company: CompanyFacts) -> None:
    check_columns(POLICY, company[1])
