"""Step 12 (BE-67, ARCH-07): every number in a summary must be one the code allows."""

import textwrap
import time
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.impact import (
    AllowedNumbers,
    NumberToken,
    allowed_numbers,
    build_facts,
    numbers_in,
    policy_thresholds,
    unknown_numbers,
)
from app.services.policy import load_policy
from app.services.shadow import Diff, TableDiff
from tests.impact_helpers import (
    DONE_WHEN_HEADLINE,
    POLICY,
    REPLIT_INTENT,
    REPLIT_SQL,
    classified,
    replit_diff,
    report,
)


def replit_allowed(intent: str = REPLIT_INTENT, sql: str = REPLIT_SQL) -> AllowedNumbers:
    classification = classified(sql)
    facts = build_facts(replit_diff(), classification, POLICY)
    return allowed_numbers(
        facts, intent=intent, sql_display=classification.sql_display, policy=POLICY
    )


def passes(text: str, allowed: AllowedNumbers | None = None) -> bool:
    return unknown_numbers(report(text), allowed or replit_allowed()) == 0


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        # Commas
        ("Deletes 48,213 users.", True),
        ("Deletes 48213 users.", True),
        ("More than 1,000 rows change.", True),
        ("It deletes 48,213, then cascades.", True),  # a trailing comma
        ("Rows 1,2,3 are gone.", False),  # 123 is invented
        ("Deletes 48,214 users.", False),
        # Percentages
        ("That is 96.4% of users.", True),
        ("That is 96% of users.", True),  # the whole number of an allowed percentage
        ("That is 98% of users.", False),  # 97 would pass: support_tickets is 96.6%
        ("That is 96.4 % of users.", True),
        ("That is 96.4 percent of users.", True),
        ("Over 50% of the table.", True),  # a threshold x 100
        ("All 100% of it.", True),
        ("That is 12% of users.", False),  # the paying count is not a share
        ("That is 12 % of users.", False),
        ("That is 12 percent of users.", False),
        ("That is 12 PERCENT of users.", False),
        # Decimals
        ("A fraction over 0.5 is reviewed.", True),
        ("Removes 96.4 users.", False),  # 96.4 is only allowed as a percentage
        ("Removes 48,213.5 users.", False),
        # Unicode digits
        ("Deletes ４８,２１３ users.", True),
        ("Deletes ４８,２１４ users.", False),
        ("Deletes ٤٨٢١٣ users.", True),
        ("Deletes ٤٨٢١٤ users.", False),
        ("Deletes ۱۳ paying users.", False),  # Extended Arabic-Indic 13
        ("That is １２％ of users.", False),  # NFKC: a full-width percent sign is still a share
        ("Deletes ¹³ paying users.", False),  # NFKC: superscript digits are not "decimal"
        # Number words
        ("Touches twelve paying customers.", True),
        ("Touches thirteen paying customers.", False),
        ("Touches Thirteen paying customers.", False),
        ("Removes half of the users.", True),  # 50% is a threshold
        ("Removes one user record per account.", True),  # "one" is exempt
        ("A dozen paying customers.", True),
        ("Thousands of rows change.", True),  # 1,000 is a threshold
        ("Around forty users are pro.", False),
        ("Removes millions of rows.", False),
        # Nothing to check
        ("Deletes inactive users and their orders.", True),
        ("", True),
    ],
)
def test_the_guardrail_on_the_replit_facts(text: str, ok: bool) -> None:
    assert passes(text) is ok


def test_the_done_when_sentence_passes_and_an_invented_count_does_not() -> None:
    assert passes(DONE_WHEN_HEADLINE)
    assert not passes(DONE_WHEN_HEADLINE.replace("12 paying", "13 paying"))


@pytest.mark.parametrize(
    ("intent", "text", "ok"),
    [
        ("Remove the 37 test users", "Removes the 37 test users the agent named.", True),
        ("Remove the 37 test users", "Removes the 38 test users.", False),
        ("Cut 5% of the free users", "The agent asked for 5% of users.", True),
        ("Cut 5% of the free users", "The agent asked for 5 users.", False),
    ],
)
def test_numbers_in_the_intent_are_allowed(intent: str, text: str, ok: bool) -> None:
    assert passes(text, replit_allowed(intent=intent)) is ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Users inactive for 6 months are removed.", True),  # interval '6 months'
        ("Users inactive for 5 months are removed.", False),
        ("Measured as of 2026-10-01.", True),  # the pinned timestamp's parts
        ("Measured as of 2027-10-01.", False),
    ],
)
def test_numbers_in_the_sql_are_allowed(text: str, ok: bool) -> None:
    assert passes(text) is ok


def test_a_list_in_the_sql_allows_each_item() -> None:
    allowed = replit_allowed(sql="DELETE FROM users WHERE id IN (17,18,19)")
    assert passes("Users 17, 18 and 19 are removed.", allowed)
    assert not passes("User 20 is removed.", allowed)


def test_the_shipped_thresholds_come_from_the_policy() -> None:
    thresholds = policy_thresholds(POLICY)
    assert {Decimal(1000), Decimal(10), Decimal(0), Decimal("0.5"), Decimal(1)} <= thresholds.plain
    assert thresholds.percents == {Decimal(50), Decimal(100)}


def test_a_changed_threshold_is_read_from_the_loaded_policy(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        textwrap.dedent(
            """
            rules:
              - { name: Big, when: { rows_affected_gt: 777 }, then: review, reason: Big. }
              - { name: Most, when: { fraction_of_table_gt: 0.25 }, then: review, reason: Most. }
            derived_groups:
              users: { paying: "plan in ('pro', 'enterprise')" }
            """
        ),
        encoding="utf-8",
    )
    custom = load_policy(path)
    classification = classified(REPLIT_SQL)
    facts = build_facts(replit_diff(), classification, custom)
    allowed = allowed_numbers(
        facts, intent=REPLIT_INTENT, sql_display=classification.sql_display, policy=custom
    )
    assert passes("More than 777 rows change.", allowed)
    assert passes("Over 25% of the table.", allowed)
    assert not passes("More than 1,000 rows change.", allowed)  # not a threshold any more
    assert not passes("More than 777 rows change.")  # the shipped file has no 777


def test_digits_inside_the_facts_names_are_allowed() -> None:
    diff = Diff(tables={"logs2024": TableDiff("logs2024", deleted=3, rows_before=9)})
    classification = classified("DELETE FROM logs2024 WHERE id < 4")
    facts = build_facts(diff, classification, POLICY)
    allowed = allowed_numbers(facts, intent="Trim", sql_display="", policy=POLICY)
    assert passes("Deletes 3 rows from logs2024.", allowed)
    assert not passes("Deletes 3 rows from logs2025.", allowed)


def test_a_number_only_in_knowledge_is_refused() -> None:
    """allowed_numbers takes no knowledge at all: chunk text is untrusted (D-149)."""
    import inspect

    assert "knowledge" not in str(inspect.signature(allowed_numbers))
    assert not passes("Enterprise accounts need 45 days of notice.")


def test_every_field_of_the_report_is_checked() -> None:
    allowed = replit_allowed()
    assert unknown_numbers(report("Fine.", key_points=["Fine.", "13 paying."]), allowed) == 1
    assert unknown_numbers(report("Fine.", concerns=["Lost 99 rows."]), allowed) == 1
    assert unknown_numbers(report("41 and 42 and 12", concerns=["99"]), allowed) == 3


def test_numbers_in_reads_values_and_percent_marks() -> None:
    assert numbers_in("48,213 rows, 96.4% and 12 percent") == [
        NumberToken(Decimal(48213), False),
        NumberToken(Decimal("96.4"), True),
        NumberToken(Decimal(12), True),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "1" + " " * 20_000,
        "1," * 10_000,
        "9" * 20_000 + "%",
        "1 " * 10_000,
        "a" * 20_000,
        "twelve " * 3_000,
        ("1" + " " * 50 + "percen") * 350,
        "１" * 20_000,
    ],
    ids=[
        "number-then-spaces",
        "commas",
        "long-percent",
        "many-numbers",
        "letters",
        "words",
        "almost-percent",
        "full-width",
    ],
)
def test_a_20_kb_adversarial_string_is_checked_in_under_a_second(text: str) -> None:
    started = time.perf_counter()
    unknown_numbers(report(text), replit_allowed())
    assert time.perf_counter() - started < 1.0
