"""DW-B14, step 14 Done-when: one write through every branch of the real graph, on the real
checkpointer and a real Blastproof database. The shadow run, the knowledge service, both models and
the commit are fakes (see `workflow_helpers`).

Per branch it checks the status history, the audit events (OBS-06), the stored decision and rules,
what was and was not called, and that a checkpoint exists for the thread (the D-175 silent loss).
"""

import uuid
from typing import Any

import pytest

from app.services.shadow import Diff, TableDiff
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL, FakeLlm, replit_diff, report
from tests.judge_helpers import FakeJudgeLlm, verdict
from tests.workflow_helpers import (
    CALM_HEADLINE,
    DROP_SQL,
    EVERY_ROW_SQL,
    FAST_LANE_SQL,
    SMALL_INSERT_SQL,
    FakeKnowledge,
    RecordingCommitter,
    WorkflowEnv,
    action_row,
    checkpoint_count,
    claim,
    event_types,
    events,
    new_action,
    rig,
    status_history,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

PRODUCTS_INSERT = Diff(tables={"products": TableDiff("products", inserted=1, rows_before=40)})
ORDERS_EVERY_ROW = Diff(tables={"orders": TableDiff("orders", deleted=51_411, rows_before=51_411)})


def rule_passes(row: dict[str, Any]) -> set[str]:
    return {rule["pass"] for rule in row["matched_rules"]}


def assert_checkpointed(env: WorkflowEnv, action_id: uuid.UUID) -> None:
    # Without autocommit the graph runs and stores nothing (D-175): prove something was stored.
    assert checkpoint_count(env, action_id) >= 1


# --- the fast lane ------------------------------------------------------------------------------


async def test_fast_lane(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, FAST_LANE_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "approved",
            "committing",
            "committed",
        ]
        assert event_types(wf_env, action_id) == [
            "received",
            "classified",
            "decided",
            "approved",
            "committing",
            "committed",
        ]
        # BE-80: no preview, no judge call, no LLM call at all.
        assert r.shadow.calls == []
        assert r.judge_llm.calls == []
        assert r.summary_llm.calls == []
        assert r.knowledge.calls == []
        assert row["status"] == "committed"
        assert row["decision"] == "allow"
        assert row["policy_reason"] == "Small insert into a notes table."
        assert row["decided_by"] is None  # auto-approval has no approver (D-037)
        assert row["decided_at"] is not None
        assert row["total_ms"] is not None
        assert row["impact_report"]["source"] == "template"
        assert row["impact"]["table_changes"] == {
            "customer_notes": {"deleted": 0, "updated": 0, "inserted": 1}
        }
        assert row["llm_cost_usd"] == 0
        assert rule_passes(row) == {"pre"}
        assert r.committer.committed == [action_id]
        assert dict(events(wf_env, action_id))["committed"] == {"stub": True, "rows": None}
        assert_checkpointed(wf_env, action_id)


# --- allow --------------------------------------------------------------------------------------


async def test_allow(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=PRODUCTS_INSERT) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "approved",
            "committing",
            "committed",
        ]
        assert event_types(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "shadow_done",
            "decided",
            "approved",
            "committing",
            "committed",
        ]
        assert len(r.shadow.calls) == 1
        assert r.shadow.calls[0]["pinned_sql"] == SMALL_INSERT_SQL
        assert len(r.summary_llm.calls) == 1
        assert len(r.judge_llm.calls) == 1
        assert row["decision"] == "allow"
        assert row["risk_level"] == "low"
        assert row["judge"]["result"]["recommendation"] == "allow"
        assert row["impact_report"]["source"] == "llm"
        assert row["preview_ms"] == 7
        assert rule_passes(row) == {"post"}
        assert r.committer.committed == [action_id]
        assert_checkpointed(wf_env, action_id)


# --- review, then approve or reject -------------------------------------------------------------


async def test_review_then_approve(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert row["status"] == "awaiting_review"
        assert row["decision"] == "review"
        assert row["expires_at"] is not None
        assert await r.next_nodes(action_id) == ("human_review",)
        assert r.committer.committed == []
        assert "Affects paying customers." in {rule["reason"] for rule in row["matched_rules"]}

        assert claim(wf_env, action_id, approved=True) == 1  # step 15's job, done by hand
        await r.resume(action_id, approved=True)

        row = action_row(wf_env, action_id)
        assert row["status"] == "committed"
        assert row["decided_by"] == wf_env.user.id
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "awaiting_review",
            "approved",
            "committing",
            "committed",
        ]
        assert r.committer.committed == [action_id]
        assert await r.next_nodes(action_id) == ()
        assert_checkpointed(wf_env, action_id)


async def test_review_then_reject(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"

        assert claim(wf_env, action_id, approved=False) == 1
        await r.resume(action_id, approved=False)

        assert action_row(wf_env, action_id)["status"] == "rejected"
        assert status_history(wf_env, action_id)[-2:] == ["awaiting_review", "rejected"]
        assert r.committer.committed == []  # the stub commit is never called
        assert await r.next_nodes(action_id) == ()


# --- block --------------------------------------------------------------------------------------


async def test_block_in_pass_one_never_previews(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, DROP_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert status_history(wf_env, action_id) == ["received", "classified", "blocked"]
        assert event_types(wf_env, action_id) == ["received", "classified", "decided", "blocked"]
        assert row["decision"] == "block"
        assert row["policy_reason"]
        assert row["total_ms"] is not None
        assert rule_passes(row) == {"pre"}
        assert r.shadow.calls == []
        assert r.summary_llm.calls == [] and r.judge_llm.calls == []
        assert r.committer.committed == []
        assert_checkpointed(wf_env, action_id)


async def test_block_in_pass_two_gets_the_free_summary_and_skips_the_judge(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=ORDERS_EVERY_ROW) as r:
        action_id = new_action(wf_env, EVERY_ROW_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert status_history(wf_env, action_id) == [
            "received",
            "classified",
            "previewing",
            "blocked",
        ]
        assert len(r.shadow.calls) == 1
        # BE-72: rules run before the judge, and a blocked write costs nothing more.
        assert r.summary_llm.calls == []
        assert r.judge_llm.calls == []
        assert r.knowledge.calls == []
        assert row["decision"] == "block"
        assert row["policy_reason"] == "Would change every row in the table."
        assert row["impact_report"]["source"] == "template"
        assert row["judge"] is None
        assert "post" in rule_passes(row)
        assert r.committer.committed == []
        assert_checkpointed(wf_env, action_id)


# --- the judge is unavailable -------------------------------------------------------------------


def decided_data(env: WorkflowEnv, action_id: uuid.UUID) -> dict[str, Any]:
    return dict(events(env, action_id))["decided"]


@pytest.mark.parametrize(
    ("judge_llm", "failure"),
    [
        (FakeJudgeLlm(raises=RuntimeError("model exploded")), "llm_error"),
        (FakeJudgeLlm(hang=True), "llm_timeout"),
        (FakeJudgeLlm(output="not even a mapping"), "parse_error"),
    ],
    ids=["raises", "hangs", "garbage"],
)
async def test_a_failing_judge_sends_the_write_to_review(
    wf_env: WorkflowEnv, judge_llm: FakeJudgeLlm, failure: str
) -> None:
    async with rig(
        wf_env, diff=PRODUCTS_INSERT, judge_llm=judge_llm, settings={"llm_timeout_seconds": 1}
    ) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert row["status"] == "awaiting_review"
        assert row["decision"] == "review"
        assert row["judge"]["failure"] == failure
        assert decided_data(wf_env, action_id)["ai_unavailable"] is True
        assert decided_data(wf_env, action_id)["deciding"] == "judge_unavailable"
        assert await r.next_nodes(action_id) == ("human_review",)
        assert_checkpointed(wf_env, action_id)


# --- the cost cap -------------------------------------------------------------------------------


async def test_a_summary_over_the_cost_cap_skips_the_judge(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=PRODUCTS_INSERT, settings={"max_llm_cost_per_action_usd": 0}) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert row["llm_cost_usd"] > 0  # the summary was billed ...
        assert r.judge_llm.calls == []  # ... so the judge is not called
        assert row["judge"]["failure"] == "skipped_cost_cap"
        assert row["status"] == "awaiting_review"
        assert decided_data(wf_env, action_id)["ai_unavailable"] is True
        assert row["impact_report"]["source"] == "llm"  # the summary is kept (BE-70)
        assert row["impact_report"]["headline"] == CALM_HEADLINE


async def test_an_unknown_summary_cost_counts_as_over_the_cap(wf_env: WorkflowEnv) -> None:
    unknown = FakeLlm(parsed=report(CALM_HEADLINE), usage=None)
    async with rig(wf_env, diff=PRODUCTS_INSERT, summary_llm=unknown) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert row["llm_cost_usd"] is None  # the cost is unknown, not zero (D-153)
        assert r.judge_llm.calls == []
        # Named for what happened, not as an overspend (gate audit 3).
        assert row["judge"]["failure"] == "cost_unknown"
        assert row["status"] == "awaiting_review"
        assert decided_data(wf_env, action_id)["ai_unavailable"] is True
        assert row["impact_report"]["headline"] == CALM_HEADLINE


# --- retrieval fails ----------------------------------------------------------------------------


async def test_a_retrieval_failure_skips_the_judge_and_goes_to_review(
    wf_env: WorkflowEnv,
) -> None:
    async with rig(wf_env, diff=PRODUCTS_INSERT, knowledge=FakeKnowledge(fails=True)) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert len(r.summary_llm.calls) == 1  # the summary still runs, without knowledge
        assert r.judge_llm.calls == []
        assert row["judge"]["failure"] == "knowledge_unavailable"
        assert row["knowledge_used"] is None
        assert row["status"] == "awaiting_review"
        assert decided_data(wf_env, action_id)["ai_unavailable"] is True


# --- audit events, every branch -----------------------------------------------------------------


async def test_the_judge_cannot_loosen_a_rule_review(wf_env: WorkflowEnv) -> None:
    """Rules before the judge: a confident `allow` leaves a rule `review` as it is."""
    allow = FakeJudgeLlm(parsed=verdict("allow", "low", 0.99, "Looks routine."))
    async with rig(wf_env, diff=replit_diff(), judge_llm=allow) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
        assert len(r.judge_llm.calls) == 1
        assert action_row(wf_env, action_id)["decision"] == "review"
        assert r.committer.committed == []


async def test_a_judge_block_blocks_an_allowed_write(wf_env: WorkflowEnv) -> None:
    block = FakeJudgeLlm(parsed=verdict("block", "critical", 0.95, "This looks unsafe."))
    async with rig(wf_env, diff=PRODUCTS_INSERT, judge_llm=block) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)

        row = action_row(wf_env, action_id)
        assert row["status"] == "blocked"
        assert row["decision"] == "block"
        assert row["risk_level"] == "critical"
        assert row["policy_reason"] == "This looks unsafe."
        assert r.committer.committed == []
        assert "shadow_done" in event_types(wf_env, action_id)


async def test_the_committer_is_not_called_for_a_blocked_or_rejected_write(
    wf_env: WorkflowEnv,
) -> None:
    committer = RecordingCommitter()
    async with rig(wf_env, committer=committer) as r:
        action_id = new_action(wf_env, DROP_SQL)
        await r.run(action_id)
    assert committer.committed == []
