"""The eval's output (backend plan §14): the text table and the JSON file."""

import json
from pathlib import Path

from app.config import DEFAULT_POLICY_FILE
from app.services.policy import load_policy
from evals.metrics import Outcome, summarize
from evals.report import format_table, write_json
from evals.rules_mode import run_rules
from tests.conftest import SCENARIOS_DIR


def sample() -> list[Outcome]:
    return [
        Outcome(
            "a", "f", "sql", "block", "block", incident=True, path="blocked", reason="r", ms=5.0
        ),
        Outcome("b", "f", "sql", "allow", "review", path="previewed", reason="r", ms=900.0),
        Outcome(
            "c",
            "f",
            "sql",
            "review",
            "review",
            path="previewed",
            reason="r",
            ms=700.0,
            summary_source="llm",
            facts_match=True,
            cost_usd=0.002,
        ),
        Outcome("r1", "reads", "read", None, None, path="read_overhead", ms=3.0),
    ]


def test_the_table_holds_every_metric() -> None:
    table = format_table(summarize(sample()))
    for text in (
        "Catch rate",
        "False block rate",
        "Exact label match",
        "number check",
        "Measured facts equal",
        "Judge unavailable",
        r"Expected \ got",
        "read_overhead",
        "previewed",
        "p50",
        "p95",
    ):
        assert text in table, text


def test_the_table_names_missed_incidents_and_errors() -> None:
    table = format_table(
        summarize(
            [
                Outcome("x", "f", "sql", "block", "allow", incident=True, reason="r"),
                Outcome("y", "f", "sql", "review", None, error="timeout"),
            ]
        )
    )
    assert "Incident replays NOT caught: x" in table
    assert "Could not be measured: y" in table


def test_the_table_of_a_rules_run_leaves_out_the_live_lines() -> None:
    outcomes = run_rules([SCENARIOS_DIR / "incidents.yaml"], load_policy(DEFAULT_POLICY_FILE))
    table = format_table(summarize(outcomes))
    assert "number check" not in table
    assert "Catch rate" in table


def test_the_json_round_trips_and_holds_no_sql_or_url(tmp_path: Path) -> None:
    outcomes = run_rules(
        [SCENARIOS_DIR / "incidents.yaml", SCENARIOS_DIR / "labeled.yaml"],
        load_policy(DEFAULT_POLICY_FILE),
    )
    path = tmp_path / "deep" / "out.json"
    write_json(summarize(outcomes), outcomes, path, meta={"mode": "rules"})
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["meta"] == {"mode": "rules"}
    assert [o["id"] for o in document["outcomes"]] == [o.id for o in outcomes]
    assert document["metrics"]["catch_rate"] == 1.0
    text = path.read_text(encoding="utf-8")
    assert "postgresql://" not in text
    assert "DELETE FROM" not in text
    assert all("sql" not in row and "intent" not in row for row in document["outcomes"])
