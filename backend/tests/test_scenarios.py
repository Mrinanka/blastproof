"""EVAL-01 to EVAL-04: the scenario files load, validate and cover the MVP incident replays."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from evals.schema import (
    HookScenario,
    ScenarioFile,
    SqlScenario,
    load_scenario_file,
)

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "evals" / "scenarios"
INCIDENTS_PATH = SCENARIOS_DIR / "incidents.yaml"
LABELED_PATH = SCENARIOS_DIR / "labeled.yaml"

ANCHOR_DATE = date(2026, 10, 1)  # D-052

# Backend plan §14 and SPEC §14: the MVP incident replays. Every one must be caught.
MVP_INCIDENTS = {
    "replit-mass-delete",
    "drop-table",
    "delete-without-where",
    "psql-bypass",
    "hook-settings-edit",
    "pocketos-lite",
}


def sql_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "sql",
        "id": "example",
        "sql": "DELETE FROM users WHERE id = 1",
        "intent": "Remove one test user",
        "expected_decision": "review",
        "decidable_from_sql": False,
    }
    entry.update(overrides)
    return entry


def hook_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "hook",
        "id": "example-hook",
        "incident": "psql-bypass",
        "tool_name": "Bash",
        "tool_input": {"command": "psql"},
        "expected_decision": "deny",
    }
    entry.update(overrides)
    return entry


def parse(*entries: dict[str, Any]) -> ScenarioFile:
    return ScenarioFile.model_validate({"anchor_date": "2026-10-01", "scenarios": list(entries)})


# --- The schema --------------------------------------------------------------------------------


def test_sql_and_hook_entries_parse_to_their_own_models() -> None:
    parsed = parse(sql_entry(), hook_entry())
    assert parsed.anchor_date == ANCHOR_DATE
    assert isinstance(parsed.scenarios[0], SqlScenario)
    assert isinstance(parsed.scenarios[1], HookScenario)


def test_expected_impact_parses() -> None:
    impact = {
        "table_changes": {"users": {"deleted": 3}, "invoices": {"updated": 2}},
        "breakdowns": {"users.plan": {"free": 3}},
        "derived_groups": {"users.paying": 0},
    }
    scenario = parse(sql_entry(expected_impact=impact)).scenarios[0]
    assert isinstance(scenario, SqlScenario)
    assert scenario.expected_impact is not None
    assert scenario.expected_impact.table_changes["users"].deleted == 3
    assert scenario.expected_impact.table_changes["invoices"].updated == 2
    assert scenario.expected_impact.table_changes["invoices"].deleted == 0


@pytest.mark.parametrize(
    "entry",
    [
        sql_entry(expected_decision="maybe"),
        sql_entry(expected_decision="deny"),  # deny is a hook outcome, not a SQL one
        hook_entry(expected_decision="block"),
        {k: v for k, v in sql_entry().items() if k != "sql"},
        sql_entry(sql=""),
        sql_entry(intent=""),
        sql_entry(tool_name="Bash"),  # a SQL entry carrying hook fields
        hook_entry(sql="SELECT 1"),  # a hook entry carrying SQL
        sql_entry(unexpected="x"),
        sql_entry(kind="shell"),
        sql_entry(id="Has Spaces"),
        sql_entry(expected_impact={"table_changes": {"users": {"deleted": -1}}}),
        sql_entry(expected_impact={"table_changes": {"users": {"removed": 1}}}),
        sql_entry(expected_impact={"table_changes": {}, "breakdowns": {"plan": {"free": 1}}}),
        sql_entry(expected_impact={"table_changes": {}, "derived_groups": {"paying": 1}}),
        hook_entry(incident=None),
        sql_entry(expected_rules=[""]),
    ],
    ids=[
        "unknown-decision",
        "deny-on-sql",
        "block-on-hook",
        "missing-sql",
        "empty-sql",
        "empty-intent",
        "sql-with-hook-field",
        "hook-with-sql",
        "extra-key",
        "unknown-kind",
        "bad-id",
        "negative-count",
        "unknown-change-type",
        "breakdown-key-without-table",
        "derived-group-key-without-table",
        "hook-without-incident",
        "empty-rule-name",
    ],
)
def test_invalid_entries_are_rejected(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse(entry)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate scenario id"):
        parse(sql_entry(id="same"), hook_entry(id="same"))


def test_a_file_without_anchor_date_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate({"scenarios": [sql_entry()]})


def test_an_empty_file_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate({"anchor_date": "2026-10-01", "scenarios": []})


def test_load_scenario_file_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump({"anchor_date": "2026-10-01", "scenarios": [sql_entry()]}),
        encoding="utf-8",
    )
    assert load_scenario_file(path).scenarios[0].id == "example"


def test_load_scenario_file_rejects_a_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scenario_file(path)


# --- The committed files -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def incidents() -> ScenarioFile:
    return load_scenario_file(INCIDENTS_PATH)


@pytest.fixture(scope="module")
def labeled() -> ScenarioFile:
    return load_scenario_file(LABELED_PATH)


def test_both_files_carry_the_anchor_date(incidents: ScenarioFile, labeled: ScenarioFile) -> None:
    assert incidents.anchor_date == ANCHOR_DATE
    assert labeled.anchor_date == ANCHOR_DATE


def test_incidents_cover_every_mvp_incident(incidents: ScenarioFile) -> None:
    assert {s.incident for s in incidents.scenarios} == MVP_INCIDENTS


def test_every_sql_incident_is_caught(incidents: ScenarioFile) -> None:
    for s in incidents.scenarios:
        if isinstance(s, SqlScenario):
            assert s.expected_decision in ("review", "block"), s.id
            if s.decidable_from_sql:
                assert s.expected_decision == "block", s.id


def test_sql_only_blocks_name_their_rules(incidents: ScenarioFile, labeled: ScenarioFile) -> None:
    for s in [*incidents.scenarios, *labeled.scenarios]:
        if isinstance(s, SqlScenario) and s.decidable_from_sql:
            assert s.expected_decision == "block", s.id
            assert s.expected_rules, s.id


def test_every_hook_incident_is_denied(incidents: ScenarioFile) -> None:
    hooks = [s for s in incidents.scenarios if isinstance(s, HookScenario)]
    assert hooks
    assert all(s.expected_decision == "deny" for s in hooks)


def test_where_true_is_among_the_delete_without_where_replays(incidents: ScenarioFile) -> None:
    sqls = [
        s.sql.lower()
        for s in incidents.scenarios
        if isinstance(s, SqlScenario) and s.incident == "delete-without-where"
    ]
    assert any("where" not in sql for sql in sqls)
    assert any("where true" in sql for sql in sqls)


def test_the_replit_replay_has_the_demo_numbers(incidents: ScenarioFile) -> None:
    (replit,) = [s for s in incidents.scenarios if s.incident == "replit-mass-delete"]
    assert isinstance(replit, SqlScenario)
    assert replit.expected_decision == "review"  # held for review, not blocked (BE-45)
    impact = replit.expected_impact
    assert impact is not None
    assert impact.table_changes["users"].deleted == 48_213
    assert impact.breakdowns["users.plan"] == {"free": 48_201, "pro": 9, "enterprise": 3}
    assert impact.derived_groups["users.paying"] == 12
    # The cascades and the SET NULL side effect are part of the expected facts.
    assert impact.table_changes["orders"].deleted > 0
    assert impact.table_changes["order_items"].deleted > 0
    assert impact.table_changes["invoices"].updated > 0


def test_labeled_set_is_sql_only_with_every_label(labeled: ScenarioFile) -> None:
    assert len(labeled.scenarios) >= 18  # about 20 now; the Evals stage tops it up to 50 (D-035)
    assert all(isinstance(s, SqlScenario) for s in labeled.scenarios)
    assert {s.expected_decision for s in labeled.scenarios} == {"allow", "review", "block"}


def test_labeled_set_has_at_least_fifty_with_every_label_well_represented(
    labeled: ScenarioFile,
) -> None:
    # EVAL-02, D-250: 50 or more. The older test above keeps its lower bound.
    assert len(labeled.scenarios) >= 50
    counts = {d: 0 for d in ("allow", "review", "block")}
    for s in labeled.scenarios:
        counts[s.expected_decision] += 1
    assert min(counts.values()) >= 7, counts


def test_every_labeled_entry_that_is_not_sql_decidable_states_its_expected_impact(
    labeled: ScenarioFile,
) -> None:
    # The rules-only eval judges these on `expected_impact`; without it the entry is an error.
    for s in labeled.scenarios:
        assert isinstance(s, SqlScenario)
        if not s.decidable_from_sql:
            assert s.expected_impact is not None, s.id


def test_ids_are_unique_across_both_files(incidents: ScenarioFile, labeled: ScenarioFile) -> None:
    ids = [s.id for s in incidents.scenarios] + [s.id for s in labeled.scenarios]
    assert len(ids) == len(set(ids))
