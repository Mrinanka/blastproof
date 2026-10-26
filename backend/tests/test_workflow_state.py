"""D-178 and SEC-14: what the graph state holds, how it round-trips, and what it must never hold."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import TypeAdapter

from app.db.enums import Decision
from app.schemas.actions import MatchedRule
from app.services.classifier import classify
from app.services.impact import build_facts
from app.services.judge import JudgeResult
from app.services.knowledge import KnowledgeRef, Retrieval, RetrievedChunk
from app.services.policy import PolicyDecision, RuleMatch, evaluate_pass_two
from app.services.shadow import Diff, RowChange, SampleRow
from app.workflow.state import (
    classification_from_state,
    classification_to_state,
    diff_from_state,
    diff_to_state,
    judge_from_state,
    matched_rules_json,
    policy_decision_from_state,
    policy_decision_to_state,
    retrieval_from_state,
    retrieval_to_state,
    rules_to_state,
)
from tests.impact_helpers import POLICY, REPLIT_CASCADES, REPLIT_SQL, replit_diff
from tests.judge_helpers import result_of

NOW = datetime(2026, 10, 1, tzinfo=UTC)
EMAIL_SQL = "DELETE FROM users WHERE email = 'planted.secret@example.com' AND last_login_at < now()"
STRICT = JsonPlusSerializer(allowed_msgpack_modules=None)


def through_checkpoint(value: Any) -> Any:
    """What a value becomes after the strict serializer the checkpointer uses."""
    return STRICT.loads_typed(STRICT.dumps_typed(value))


def test_classification_round_trips_but_for_the_pinned_sql() -> None:
    original = classify(EMAIL_SQL, now=NOW)
    state = classification_to_state(original)
    assert state["pinned_sql"] == ""
    assert classification_from_state(state, original.pinned_sql) == original
    assert classification_from_state(state).pinned_sql == ""


def test_classification_state_is_plain_json() -> None:
    state = classification_to_state(classify(REPLIT_SQL, now=NOW))
    assert json.loads(json.dumps(state)) == state
    assert through_checkpoint(state) == state


def test_the_state_never_holds_the_sql_or_the_planted_literal() -> None:
    classification = classify(EMAIL_SQL, now=NOW)
    assert "planted.secret" in classification.pinned_sql  # the planted value is real
    state = classification_to_state(classification)
    assert "planted.secret" not in json.dumps(state)
    assert "[masked]" in state["sql_display"]
    # sql_display is masked and is the only SQL text in the state.
    others = {k: v for k, v in state.items() if k != "sql_display"}
    assert "DELETE" not in json.dumps(others)


def test_the_fast_lane_row_count_survives() -> None:
    sql = "INSERT INTO customer_notes (user_id, body) VALUES (1, 'a'), (2, 'b')"
    original = classify(sql, now=NOW)
    again = classification_from_state(classification_to_state(original), original.pinned_sql)
    assert again.insert_rows == 2


def test_diff_round_trips_without_row_changes() -> None:
    samples = (SampleRow("users", "deleted", "before", {"id": 1, "email": "[email]"}),)
    base = replit_diff(samples=samples)
    original = Diff(
        base.tables,
        base.breakdowns,
        base.derived_groups,
        base.cascade_expectations,
        base.samples,
        tuple(RowChange("users", {"id": i}, "rowhash", "deleted") for i in range(5)),
    )
    state = diff_to_state(original)
    # Only the summary: no per-row key or hash reaches a checkpoint.
    assert set(state) == {"impact", "cascade"}
    assert "rowhash" not in json.dumps(state)
    again = diff_from_state(through_checkpoint(state))
    assert again.changes == ()
    assert again.tables == original.tables
    assert again.breakdowns == original.breakdowns
    assert again.derived_groups == original.derived_groups
    assert again.samples == original.samples
    assert again.facts() == original.facts()
    assert again.impact_json() == original.impact_json()
    assert again.cascade_json() == original.cascade_json()


def test_the_rebuilt_diff_gives_the_same_facts_and_rule_decision() -> None:
    original = replit_diff()
    classification = classify(REPLIT_SQL, now=NOW)
    again = diff_from_state(diff_to_state(original))
    assert build_facts(again, classification, POLICY) == build_facts(
        original, classification, POLICY
    )
    assert evaluate_pass_two(POLICY, classification, again.facts()) == evaluate_pass_two(
        POLICY, classification, original.facts()
    )


def test_cascade_paths_keep_their_names() -> None:
    again = diff_from_state(diff_to_state(replit_diff(cascades=REPLIT_CASCADES)))
    assert len(again.cascade_expectations) == len(REPLIT_CASCADES)
    for new, old in zip(again.cascade_expectations, REPLIT_CASCADES, strict=True):
        assert (new.target, new.table, new.on_delete, new.count) == (
            old.target,
            old.table,
            old.on_delete,
            old.count,
        )
        assert new.to_json() == old.to_json()


def test_retrieval_round_trips_and_masks_the_chunk_text() -> None:
    chunk = RetrievedChunk(
        "c1", "notes.md", ["users"], "Call jane.doe@example.com about renewals.", "table", None
    )
    ref = KnowledgeRef("c1", "notes.md", ["users"], "Call", "table", None)
    state = retrieval_to_state(Retrieval([chunk], [ref], 12))
    assert "jane.doe" not in json.dumps(state)
    again = retrieval_from_state(through_checkpoint(state))
    assert again is not None
    assert again.refs == [ref]
    assert again.duration_ms == 12
    assert again.chunks[0].content != chunk.content
    assert again.chunks[0].chunk_id == "c1"
    assert retrieval_from_state(None) is None


def test_the_policy_decision_round_trips() -> None:
    decision = PolicyDecision(
        Decision.REVIEW,
        (RuleMatch("A", Decision.REVIEW, "because"), RuleMatch("B", Decision.ALLOW, "fine")),
        "because",
    )
    matched = rules_to_state(decision.matched)
    assert policy_decision_from_state(policy_decision_to_state(decision), matched) == decision


def test_matched_rules_json_has_exactly_the_shape_the_views_read() -> None:
    pre = [{"name": "A", "outcome": "review", "reason": "r1"}]
    post = [{"name": "B", "outcome": "block", "reason": "r2"}]
    stored = matched_rules_json(pre, post)
    assert stored == [
        {"name": "A", "outcome": "review", "reason": "r1", "pass": "pre"},
        {"name": "B", "outcome": "block", "reason": "r2", "pass": "post"},
    ]
    assert matched_rules_json([], []) == []
    # The step 19 view parser (D-170) accepts it.
    parsed = TypeAdapter(list[MatchedRule]).validate_python(stored)
    assert [(r.name, r.outcome, r.reason, r.pass_) for r in parsed] == [
        ("A", Decision.REVIEW, "r1", "pre"),
        ("B", Decision.BLOCK, "r2", "post"),
    ]


def test_the_judge_result_round_trips() -> None:
    blocked = result_of("block").verdict
    for original in (
        result_of("allow"),
        JudgeResult.unavailable("llm", "knowledge_unavailable"),
        JudgeResult("llm", None, None, "llm_error", False, None, None, None, 4),
        JudgeResult("llm", "m", blocked, None, True, 1, 2, Decimal("0.0123"), 9),
    ):
        assert judge_from_state(through_checkpoint(original.to_json())) == original


@dataclass
class Sneaky:
    value: int


def test_strict_serde_does_not_revive_a_dataclass() -> None:
    """A dataclass that gets into the state comes back as a plain dict, never as an instance."""
    revived = through_checkpoint({"inner": Sneaky(1)})
    assert not isinstance(revived["inner"], Sneaky)
