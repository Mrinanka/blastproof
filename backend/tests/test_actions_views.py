"""The action views, without a database: each JSONB writer's real output parses, and the
masking, caps and flags hold (BE-109, BE-110, ARCH-17)."""

import asyncio
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.config import DEFAULT_POLICY_FILE
from app.db.actions import DetailRows, SummaryRow
from app.db.models import Action, Agent, AuditEvent, User
from app.logging import configure_logging
from app.services import actions as service
from app.services import classifier
from app.services.actions import StoredShapeError, build_detail, build_summary, can_decide
from app.services.judge import JudgeResult
from app.services.knowledge import KnowledgeRef
from app.services.policy import load_policy
from app.services.shadow import Diff, TableDiff
from tests.action_helpers import sample_diff, sample_judge, sample_report

POLICY = load_policy(DEFAULT_POLICY_FILE)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
PINNED = {"now()": "2026-10-01T00:00:00Z"}


def rows_for(action: Action, *, events: list[AuditEvent] | None = None) -> DetailRows:
    owner = User(id=uuid.uuid4(), display_name="Owner")
    agent = Agent(id=uuid.uuid4(), name="agent")
    return DetailRows(action, agent, owner, None, events or [], None)


def make_action(**columns: Any) -> Action:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": "awaiting_review",
        "tables": ["users"],
        "created_at": NOW,
        "pinned_values": None,
    }
    return Action(**(values | columns))


def detail(action: Action, **kwargs: Any):  # type: ignore[no-untyped-def]
    return asyncio.run(build_detail(rows_for(action, **kwargs), POLICY, NOW))


# --- each writer's real output parses -----------------------------------------------------------


def test_impact_json_from_a_diff_parses() -> None:
    result = detail(make_action(impact=sample_diff().impact_json()))
    assert [(t.table, t.deleted, t.is_cascade) for t in result.table_changes] == [
        ("users", 10, False),
        ("orders", 4, True),
    ]
    assert result.breakdowns[0].counts == {"free": 8, "pro": 2}
    assert result.samples[0].values["email"] == "[email]"


def test_report_json_from_an_impact_result_parses() -> None:
    result = detail(make_action(impact_report=sample_report().report_json()))
    assert result.impact_report is not None
    assert result.impact_report.source == "llm"
    template = sample_report().report_json() | {"source": "template", "fallback_reason": "x"}
    shown = detail(make_action(impact_report=template)).impact_report
    assert shown is not None and shown.source == "template"


def test_judge_json_for_a_verdict_a_withheld_reason_and_each_failure() -> None:
    shown = detail(make_action(judge=sample_judge().to_json())).judge
    assert shown is not None and shown.recommendation == "review"
    assert shown.reason == "Paying users."

    withheld = JudgeResult("llm", "m", sample_judge().verdict, None, True, 1, 1, None, 1).to_json()
    shown = detail(make_action(judge=withheld)).judge
    assert shown is not None and shown.recommendation == "review" and shown.reason is None

    for failure in (
        "not_configured",
        "llm_timeout",
        "llm_error",
        "parse_error",
        "bad_shape",
        "judge_error",
        "skipped_cost_cap",
    ):
        stored = JudgeResult.unavailable("llm", failure).to_json()  # type: ignore[arg-type]
        shown = detail(make_action(judge=stored)).judge
        assert shown is not None and shown.failure == failure
        assert shown.recommendation is None and shown.confidence is None


def test_knowledge_refs_parse_and_the_first_line_is_masked() -> None:
    ref = KnowledgeRef("c1", "notes.md", ["users"], "Ask bob@example.com", "similar", 0.42)
    [shown] = detail(make_action(knowledge_used=[asdict(ref)])).knowledge_used
    assert shown.first_line == "Ask [email]" and shown.score == 0.42 and shown.match == "similar"


def test_matched_rules_keep_the_pass_alias() -> None:
    stored = [{"name": "N", "outcome": "block", "reason": "R", "pass": "pre"}]
    [rule] = detail(make_action(matched_rules=stored)).matched_rules
    assert rule.pass_ == "pre"
    assert rule.model_dump(by_alias=True)["pass"] == "pre"


# --- derived groups, samples, timeline ----------------------------------------------------------


def test_derived_group_definition_comes_from_the_policy_and_is_none_when_gone() -> None:
    diff = Diff(
        tables={"users": TableDiff("users", deleted=1, rows_before=2)},
        derived_groups={"users.paying": 1, "users.retired_group": 3},
    )
    groups = {g.name: g for g in detail(make_action(impact=diff.impact_json())).derived_groups}
    assert groups["paying"].definition == POLICY.derived_groups["users"]["paying"].condition
    assert groups["retired_group"].definition is None and groups["retired_group"].count == 3


def test_samples_are_cut_to_50_keeping_the_stored_order() -> None:
    diff = sample_diff(samples=120)
    shown = detail(make_action(impact=diff.impact_json())).samples
    assert len(shown) == 50
    assert [s.values["id"] for s in shown] == list(range(50))


def test_timeline_data_keeps_scalars_only_and_cuts_and_masks_text() -> None:
    kept = service.timeline_data(
        {
            "n": 3,
            "f": 1.5,
            "ok": True,
            "nothing": None,
            "text": "a" * 500,
            "mail": "write to bob@example.com",
            "nested": {"a": 1},
            "list": [1, 2],
        }
    )
    assert set(kept) == {"n", "f", "ok", "nothing", "text", "mail"}
    assert kept["text"] == "a" * 200
    assert kept["mail"] == "write to [email]"


def test_timeline_events_come_out_in_the_order_given() -> None:
    events = [
        AuditEvent(event_type="received", created_at=NOW, data={}),
        AuditEvent(event_type="classified", created_at=NOW, data={"n": 1}),
    ]
    shown = detail(make_action(), events=events).timeline
    assert [e.event_type for e in shown] == ["received", "classified"]


# --- flags and masking --------------------------------------------------------------------------

OTHER_STATUSES = (
    "received",
    "classified",
    "previewing",
    "approved",
    "rejected",
    "expired",
    "blocked",
    "committing",
    "committed",
    "drifted",
    "executed",
    "failed",
)


@pytest.mark.parametrize(
    ("status", "expires", "expected"),
    [
        ("awaiting_review", None, True),
        ("awaiting_review", NOW + timedelta(seconds=1), True),
        ("awaiting_review", NOW, False),
        ("awaiting_review", NOW - timedelta(seconds=1), False),
        *[(status, None, False) for status in OTHER_STATUSES],
    ],
)
def test_can_decide_for_every_status(status: str, expires: datetime | None, expected: bool) -> None:
    assert can_decide(status, expires, NOW) is expected


def test_intent_is_masked_in_the_summary_and_the_detail() -> None:
    intent = "Email jane.doe@example.com or call +1 415 555 0132"
    row = SummaryRow(
        id=uuid.uuid4(),
        status="executed",
        decision=None,
        risk_level=None,
        intent=intent,
        headline=None,
        tables=[],
        created_at=NOW,
        agent_id=uuid.uuid4(),
        agent_name="a",
        decider_id=None,
        decider_name=None,
    )
    for shown in (build_summary(row).intent, detail(make_action(intent=intent)).intent):
        assert shown is not None
        assert "jane.doe" not in shown and "555" not in shown


def test_original_sql_display_only_when_something_was_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = "DELETE FROM users WHERE email = 'a.b@example.com' AND at < now()"
    assert detail(make_action(sql=sql)).original_sql_display is None
    shown = detail(make_action(sql=sql, pinned_values=PINNED))
    assert shown.original_sql_display is not None
    assert "a.b@example.com" not in shown.original_sql_display

    async def timed_out(_sql: str) -> str | None:
        return None

    monkeypatch.setattr(service, "display_in_worker", timed_out)
    assert detail(make_action(sql=sql, pinned_values=PINNED)).original_sql_display is None


def test_display_in_worker_gives_up_after_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classifier, "CLASSIFY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(classifier, "display_sql", lambda sql: time.sleep(0.5) or sql)
    assert asyncio.run(classifier.display_in_worker("SELECT 1")) is None


def test_display_in_worker_masks() -> None:
    shown = asyncio.run(classifier.display_in_worker("SELECT 1 WHERE e = 'a.b@example.com'"))
    assert shown is not None and "a.b@example.com" not in shown


def test_llm_cost_is_a_float() -> None:
    assert detail(make_action(llm_cost_usd=Decimal("0.001500"))).llm_cost_usd == 0.0015


# --- malformed stored JSON fails loudly ---------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("impact_report", {"headline": "only"}),
        ("impact_report", ["not", "a", "dict"]),
        ("impact", {"table_changes": {}}),
        ("impact", {**sample_diff().impact_json(), "derived_groups": {"nodot": 1}}),
        ("impact", {**sample_diff().impact_json(), "breakdowns": {"nodot": {"a": 1}}}),
        ("judge", {"provider": "llm"}),
        ("judge", {**sample_judge().to_json(), "result": {"recommendation": "maybe"}}),
        ("matched_rules", [{"name": "n"}]),
        ("matched_rules", {"name": "n"}),
        ("knowledge_used", [{"chunk_id": "x"}]),
    ],
)
def test_a_wrong_stored_shape_raises(column: str, value: Any) -> None:
    with pytest.raises(StoredShapeError):
        detail(make_action(**{column: value}))


def test_the_error_names_the_field_and_never_the_value() -> None:
    with pytest.raises(StoredShapeError) as caught:
        detail(make_action(impact_report={"headline": "secret text", "key_points": 5}))
    assert caught.value.field == "impact_report"
    assert "secret text" not in str(caught.value)


def test_the_log_names_the_field_and_keys_and_holds_no_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # caplog, not structlog's capture_logs: after configure_logging the logger is cached.
    configure_logging(stream=None)  # alone, structlog is unconfigured and bypasses logging
    bad = {"headline": "secret text", "key_points": 5}
    with caplog.at_level("ERROR"), pytest.raises(StoredShapeError):
        detail(make_action(impact_report=bad))
    [entry] = [r.msg for r in caplog.records if r.msg.get("event") == "stored_shape_invalid"]
    assert entry["field"] == "impact_report" and "key_points" in entry["locations"]
    assert "secret text" not in str(caplog.records)
