"""The write workflow (backend plan step 14): every write runs through this one checkpointed graph.

    classify -> pre_rules -+-> block
                           +-> fast_lane -> approve
                           +-> shadow -> post_rules -+-> block
                                                     +-> retrieve -> impact -> judge -> decide
    decide -+-> approve ---------------------------------> commit
            +-> request_review -> human_review -(approved)-> commit
            +-> block
    commit -+-> end
            +-> shadow   (the data changed since the preview: once, then it fails)

Rules (CLAUDE.md, architecture rules):

* Fail closed. Every node but `human_review` runs inside `fail_closed`: any exception ends the
  action as `failed` with decision `block` and a `fail_closed` event, and the graph ends normally.
* The commit applies the write only if the company data still matches the preview. On a difference
  it rolls back and previews again; the new preview always goes to a person, never auto-approved
  (BE-92); a second difference fails the write. A commit whose outcome is unknown stays `committing`
  for the sweep to settle (`CommitOutcomeUnknownError` passes through the fail-closed wrapper).
* Reads never come here (ARCH-10); a read handed to the graph fails with `not_a_write`.
* Rules run before the judge, the judge can only make a decision stricter (`decide`), and a pass-2
  block never reaches retrieval, the LLM or the judge.
* Review is two nodes. `interrupt()` re-runs its whole node on resume, so the status change lives in
  `request_review` and `human_review` holds only the `interrupt()` call (D-025, SPIKE-04).
* The state holds no raw agent text (see `state.py`).
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.clock import Clock
from app.db.actions import add_event, set_status
from app.db.enums import ALLOWED_TRANSITIONS, ActionStatus, AuditEventType, Decision
from app.db.impact import save_impact
from app.db.judge import save_judge
from app.db.knowledge import save_knowledge_used
from app.db.models import Action, System
from app.db.preview import save_preview
from app.services.classifier import Kind, classify_in_worker
from app.services.commit import (
    CommitDriftError,
    CommitFailedError,
    CommitOutcomeUnknownError,
    Committer,
)
from app.services.impact import ImpactEngine, ImpactReport, build_facts
from app.services.judge import (
    Judge,
    JudgeContext,
    JudgeResult,
    assess_safely,
    decide,
    injection_signals,
)
from app.services.knowledge import KnowledgeService, RetrievalError
from app.services.masking import mask_text
from app.services.policy import (
    Policy,
    PolicyFactsError,
    Route,
    evaluate_pass_one,
    evaluate_pass_two,
)
from app.services.shadow import Diff, ShadowBusyError, ShadowFailedError, TableDiff
from app.workflow.state import (
    WriteState,
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

logger = structlog.get_logger()

REVIEW_NODE = "human_review"
# The first drift previews again; the second fails the write.
MAX_DRIFTS = 2
# The judge's own timeout is the model timeout plus a margin, as the CLI uses.
JUDGE_TIMEOUT_MARGIN_SECONDS = 5


class FailClosed(Exception):
    """A known reason to stop the write. `reason_code` goes to the agent in the `failed` message."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class NotAwaitingReviewError(RuntimeError):
    """`resume_write` was called for a thread that is not paused at `human_review` (D-177)."""


@dataclass(frozen=True)
class WorkflowDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    policy: Policy
    shadow: Any  # ShadowRunner, or a fake with the same `run`
    impact: ImpactEngine
    judge: Judge
    knowledge: KnowledgeService | None
    committer: Committer
    clock: Clock
    settings: Settings


def run_config(action_id: uuid.UUID, agent_id: uuid.UUID, env: str) -> dict[str, Any]:
    """The config of one run: `thread_id` is the action (D-025); the metadata names it in traces."""
    return {
        "configurable": {"thread_id": str(action_id)},
        "run_name": "write_pipeline",
        "metadata": {"action_id": str(action_id), "agent_id": str(agent_id), "env": env},
    }


# --- Routing (pure) -----------------------------------------------------------------------------


def _ended(state: WriteState) -> bool:
    return state.get("outcome") is not None


def next_unless_ended(target: str) -> Callable[[WriteState], str]:
    """The edge that goes to `target`, or ends the run when a node failed closed."""

    def route(state: WriteState) -> str:
        return END if _ended(state) else target

    return route


def route_after_pre_rules(state: WriteState) -> str:
    if _ended(state):
        return END
    return {Route.BLOCK: "block", Route.FAST_LANE: "fast_lane"}.get(Route(state["route"]), "shadow")


def route_after_post_rules(state: WriteState) -> str:
    if _ended(state):
        return END
    return "block" if state["post_decision"]["decision"] == Decision.BLOCK else "retrieve"


def route_after_decide(state: WriteState) -> str:
    if _ended(state):
        return END
    return {Decision.ALLOW: "approve", Decision.REVIEW: "request_review"}.get(
        Decision(state["decision"]), "block"
    )


def route_after_commit(state: WriteState) -> str:
    """A drift sends the run back to the preview; anything else ends it."""
    if _ended(state):
        return END
    return "shadow" if state.get("repreview") else END


def after_drift(decision: Decision, reason: str, drifts: int) -> tuple[Decision, str]:
    """After a drift an `allow` becomes `review`: a person looks at the new preview (BE-92). A
    `review` stays and a `block` stays, so only the decision's strictness can go up."""
    if drifts > 0 and decision == Decision.ALLOW:
        return Decision.REVIEW, (
            "The company data changed after the first preview, so the new preview needs a "
            f"person's approval. {reason}"
        )
    return decision, reason


def route_after_human_review(state: WriteState) -> str:
    """Only an exact approval commits. Anything else, even a truthy look-alike, ends the run."""
    review = state.get("review")
    # `is True`, not `==`: in Python {"approved": 1} == {"approved": True}.
    exact = isinstance(review, dict) and set(review) == {"approved"} and review["approved"] is True
    return "commit" if exact else END


def over_cost_cap(cost: Decimal | None, cap: Decimal) -> bool:
    """An unknown cost counts as over (BE-70); a cost equal to the cap is not over."""
    return cost is None or cost > cap


# --- The graph ----------------------------------------------------------------------------------


class _Nodes:
    def __init__(self, deps: WorkflowDeps) -> None:
        self.deps = deps

    # -- helpers --

    def _tx(self) -> Any:
        return self.deps.sessionmaker.begin()

    @staticmethod
    def _id(state: WriteState) -> uuid.UUID:
        return uuid.UUID(state["action_id"])

    def _classification(self, state: WriteState, pinned_sql: str = "") -> Any:
        return classification_from_state(state["classification"], pinned_sql)

    def _judge_context(self, state: WriteState) -> JudgeContext:
        classification = self._classification(state)
        diff = diff_from_state(state["diff"])
        return JudgeContext(
            action_id=self._id(state),
            agent_id=uuid.UUID(state["agent_id"]),
            intent=state["intent_masked"],
            classification=classification,
            diff=diff,
            facts=build_facts(diff, classification, self.deps.policy),
            report=ImpactReport.model_validate(state["impact_report"]),
            rules=policy_decision_from_state(state["post_decision"], state["post_rules"]),
            retrieval=retrieval_from_state(state.get("knowledge")),
        )

    @staticmethod
    def _total_ms(state: WriteState, started: float) -> int:
        return state.get("work_ms", 0) + round((time.perf_counter() - started) * 1000)

    async def _record_decision(
        self, session: AsyncSession, state: WriteState, *, risk_level: str | None = None
    ) -> None:
        """Write the decision, its reason, the matched rules and the `decided` event."""
        values: dict[str, Any] = {
            "decision": state["decision"],
            "policy_reason": state["reason"],
            "matched_rules": matched_rules_json(
                state.get("pre_rules", []), state.get("post_rules", [])
            ),
        }
        if risk_level is not None:
            values["risk_level"] = risk_level
        action_id = self._id(state)
        await session.execute(update(Action).where(Action.id == action_id).values(**values))
        await add_event(
            session,
            action_id,
            AuditEventType.DECIDED,
            {
                "decision": state["decision"],
                "deciding": state["deciding"],
                "ai_unavailable": state.get("ai_unavailable", False),
                "injection": state.get("injection", []),
            },
        )

    # -- nodes --

    async def classify(self, state: WriteState) -> dict[str, Any]:
        action_id = self._id(state)
        async with self._tx() as session:
            action = await session.get(Action, action_id)
            if action is None:
                raise FailClosed("action_not_found")
            system = await session.get(System, action.system_id)
            if system is None:
                raise FailClosed("system_not_found")
            sql, intent = action.sql or "", action.intent or ""
            agent_id, profile = action.agent_id, system.connection_profile
        classification, duration_ms = await classify_in_worker(sql, self.deps.clock)
        logger.info("classified", kind=classification.kind.value, duration_ms=duration_ms)
        if classification.kind == Kind.READ:
            raise FailClosed("not_a_write")  # ARCH-10: reads never enter the graph
        async with self._tx() as session:
            await session.execute(
                update(Action)
                .where(Action.id == action_id)
                .values(
                    pinned_sql=classification.pinned_sql,
                    sql_display=classification.sql_display,
                    pinned_values=classification.pinned_values,
                    statement_type=classification.statement_type,
                    tables=list(classification.tables),
                )
            )
            await set_status(
                session,
                action_id,
                ActionStatus.CLASSIFIED,
                data={"kind": classification.kind.value, "duration_ms": duration_ms},
            )
        return {
            "agent_id": str(agent_id),
            "profile": profile,
            "intent_masked": mask_text(intent),
            "classification": classification_to_state(classification),
        }

    async def pre_rules(self, state: WriteState) -> dict[str, Any]:
        result = evaluate_pass_one(self.deps.policy, self._classification(state))
        update_: dict[str, Any] = {
            "route": result.route.value,
            "pre_rules": rules_to_state(result.matched),
            "reason": result.reason,
            "deciding": "rules",
        }
        if result.route == Route.BLOCK:
            update_["decision"] = Decision.BLOCK.value
        elif result.route == Route.FAST_LANE:
            update_["decision"] = Decision.ALLOW.value
        return update_

    async def fast_lane(self, state: WriteState) -> dict[str, Any]:
        """A small plain INSERT: no preview, no judge, no LLM call (BE-80). The diff is predicted
        from the VALUES list, so the summary and step 16's row-count check have a number."""
        classification = self._classification(state)
        if classification.insert_rows is None or len(classification.target_tables) != 1:
            raise FailClosed("fast_lane_invalid")
        table = classification.target_tables[0]
        # `rows_before` is not measured (0): the fast lane reads nothing from the company database.
        diff = Diff(tables={table: TableDiff(table, inserted=classification.insert_rows)})
        result = self.deps.impact.template(classification=classification, diff=diff)
        async with self._tx() as session:
            await session.execute(
                update(Action)
                .where(Action.id == self._id(state))
                .values(impact=diff.impact_json(), cascade_expectations=[])
            )
            await save_impact(session, self._id(state), result)
        return {"diff": diff_to_state(diff), "impact_report": result.report_json()}

    async def shadow(self, state: WriteState) -> dict[str, Any]:
        action_id = self._id(state)
        async with self._tx() as session:
            await set_status(session, action_id, ActionStatus.PREVIEWING)
            action = await session.get(Action, action_id)
            assert action is not None
            pinned_sql = action.pinned_sql or ""
        # ShadowBusyError and ShadowFailedError end the run as `failed` with their reason code.
        result = await self.deps.shadow.run(
            action_id=action_id,
            profile=state["profile"],
            classification=self._classification(state, pinned_sql),
        )
        async with self._tx() as session:
            await save_preview(session, action_id, result.diff, result.preview_ms)
            await add_event(
                session,
                action_id,
                AuditEventType.SHADOW_DONE,
                {"preview_ms": result.preview_ms, "rows_changed": len(result.diff.changes)},
            )
        # A re-preview after a drift starts the decision over (`decided`, `repreview` cleared).
        return {"diff": diff_to_state(result.diff), "repreview": False, "decided": False}

    async def post_rules(self, state: WriteState) -> dict[str, Any]:
        diff = diff_from_state(state["diff"])
        try:
            decision = evaluate_pass_two(
                self.deps.policy, self._classification(state), diff.facts()
            )
        except PolicyFactsError:
            raise FailClosed("policy_facts") from None
        update_: dict[str, Any] = {
            "post_rules": rules_to_state(decision.matched),
            "post_decision": policy_decision_to_state(decision),
        }
        if decision.decision == Decision.BLOCK:
            # Blocked on the measured facts: the free template summary, and no retrieval, LLM or
            # judge (BE-72).
            result = self.deps.impact.template(
                classification=self._classification(state), diff=diff
            )
            async with self._tx() as session:
                await save_impact(session, self._id(state), result)
            update_ |= {
                "decision": Decision.BLOCK.value,
                "reason": decision.reason,
                "deciding": "rules",
                "impact_report": result.report_json(),
            }
        return update_

    async def retrieve(self, state: WriteState) -> dict[str, Any]:
        knowledge = self.deps.knowledge
        if knowledge is None:
            return {"knowledge": None, "knowledge_failed": True}
        classification = self._classification(state)
        try:
            retrieval = await knowledge.retrieve(state["intent_masked"], classification.tables)
        except RetrievalError:
            # Go on without knowledge; the judge is then skipped (see `judge`).
            return {"knowledge": None, "knowledge_failed": True}
        async with self._tx() as session:
            await save_knowledge_used(session, self._id(state), retrieval.refs)
        return {"knowledge": retrieval_to_state(retrieval), "knowledge_failed": False}

    async def impact(self, state: WriteState) -> dict[str, Any]:
        result = await self.deps.impact.summarize(
            action_id=self._id(state),
            agent_id=uuid.UUID(state["agent_id"]),
            intent=state["intent_masked"],
            classification=self._classification(state),
            diff=diff_from_state(state["diff"]),
            retrieval=retrieval_from_state(state.get("knowledge")),
        )
        async with self._tx() as session:
            await save_impact(session, self._id(state), result)
        return {"impact_report": result.report_json()}

    async def judge(self, state: WriteState) -> dict[str, Any]:
        action_id = self._id(state)
        provider = str(getattr(self.deps.judge, "provider", "unknown"))
        async with self._tx() as session:
            action = await session.get(Action, action_id)
            assert action is not None
            cost = action.llm_cost_usd
        cap = Decimal(str(self.deps.settings.max_llm_cost_per_action_usd))
        if over_cost_cap(cost, cap):
            result = JudgeResult.unavailable(provider, "skipped_cost_cap")
        elif state.get("knowledge_failed"):
            # A judge without the business rules is the unreliable case: skip it, go to review.
            result = JudgeResult.unavailable(provider, "knowledge_unavailable")
        else:
            result = await assess_safely(
                self.deps.judge,
                self._judge_context(state),
                self.deps.settings.llm_timeout_seconds + JUDGE_TIMEOUT_MARGIN_SECONDS,
            )
        async with self._tx() as session:
            await save_judge(session, action_id, result)
        return {"judge": result.to_json()}

    async def decide(self, state: WriteState) -> dict[str, Any]:
        context = self._judge_context(state)
        final = decide(
            context.rules,
            judge_from_state(state["judge"]),
            injection_signals(context),
            min_confidence=self.deps.settings.judge_min_confidence,
        )
        decision, reason = after_drift(final.decision, final.reason, state.get("drifts", 0))
        update_: dict[str, Any] = {
            "decision": decision.value,
            "reason": reason,
            "deciding": "drift" if decision != final.decision else final.deciding,
            "ai_unavailable": final.ai_unavailable,
            "injection": [f"{hit.field}:{hit.pattern}" for hit in final.injection],
            "decided": True,
        }
        async with self._tx() as session:
            await self._record_decision(session, {**state, **update_}, risk_level=final.risk_level)
        return update_

    async def approve(self, state: WriteState) -> dict[str, Any]:
        """Auto-approval (D-037): approved with no approver."""
        async with self._tx() as session:
            if not state.get("decided"):  # the fast lane never went through `decide`
                await self._record_decision(session, state)
            await set_status(
                session,
                self._id(state),
                ActionStatus.APPROVED,
                decided_at=self.deps.clock.now(),
            )
        return {"decided": True}

    async def request_review(self, state: WriteState) -> dict[str, Any]:
        hours = self.deps.settings.review_timeout_hours
        async with self._tx() as session:
            await set_status(
                session,
                self._id(state),
                ActionStatus.AWAITING_REVIEW,
                expires_at=self.deps.clock.now() + timedelta(hours=hours),
            )
        return {}

    async def block(self, state: WriteState) -> dict[str, Any]:
        started = time.perf_counter()
        async with self._tx() as session:
            if not state.get("decided"):
                await self._record_decision(session, state)
            await set_status(
                session,
                self._id(state),
                ActionStatus.BLOCKED,
                total_ms=self._total_ms(state, started),
            )
        return {"decided": True}

    async def commit(self, state: WriteState) -> dict[str, Any]:
        started = time.perf_counter()
        action_id = self._id(state)
        async with self._tx() as session:
            action = await session.get(Action, action_id, with_for_update=True)
            assert action is not None
            if action.status == ActionStatus.COMMITTING.value:
                # A continuation after a crash or a lost connection: the committer settles it from
                # the marker in the company database. No status change.
                logger.warning("commit_reconcile")
            elif action.status != ActionStatus.APPROVED.value:
                # The approval no longer stands (expired, rejected, already committed): change
                # nothing and never commit.
                await add_event(
                    session,
                    action_id,
                    AuditEventType.FAIL_CLOSED,
                    {"reason": "resume_mismatch", "status": action.status},
                )
                logger.error("fail_closed", reason_code="resume_mismatch", node="commit")
                return {"outcome": "mismatch"}
            else:
                await set_status(session, action_id, ActionStatus.COMMITTING)
        try:
            outcome = await self.deps.committer.commit(action_id)
        except CommitDriftError as drift:
            return await self._drifted(state, drift, started)
        except CommitFailedError as exc:
            raise FailClosed(exc.reason_code) from None
        data: dict[str, Any] = {"stub": outcome.stub, "rows": outcome.rows}
        if not outcome.stub:
            data["already_applied"] = outcome.already_applied
        try:
            async with self._tx() as session:
                await set_status(
                    session,
                    action_id,
                    ActionStatus.COMMITTED,
                    total_ms=self._total_ms(state, started),
                    data=data,
                )
        except Exception:
            # The company database has the write. Failing here would say it did not: stay
            # `committing`, and the rerun finds the marker and writes `committed`.
            raise CommitOutcomeUnknownError from None
        return {"repreview": False}

    async def _drifted(
        self, state: WriteState, drift: CommitDriftError, started: float
    ) -> dict[str, Any]:
        """The data changed since the preview and nothing was applied. The first time: back to the
        preview, with the approver cleared (the new preview needs its own approval). The second:
        failed."""
        action_id = self._id(state)
        attempt = state.get("drifts", 0) + 1
        logger.warning("commit_drifted", attempt=attempt, reason=drift.reason)
        async with self._tx() as session:
            await set_status(
                session,
                action_id,
                ActionStatus.DRIFTED,
                data={"attempt": attempt, "reason": drift.reason, "counts": drift.counts},
            )
            await session.execute(
                update(Action)
                .where(Action.id == action_id)
                .values(decided_by=None, decided_at=None)
            )
            if attempt >= MAX_DRIFTS:
                await set_status(
                    session,
                    action_id,
                    ActionStatus.FAILED,
                    failure_reason="drifted",
                    total_ms=self._total_ms(state, started),
                    decision=Decision.BLOCK,
                    data={"reason": "drifted"},
                )
                await add_event(
                    session,
                    action_id,
                    AuditEventType.FAIL_CLOSED,
                    {"reason": "drifted", "node": "commit"},
                )
        if attempt >= MAX_DRIFTS:
            return {"outcome": "failed", "drifts": attempt}
        return {"drifts": attempt, "repreview": True}

    async def human_review(self, state: WriteState) -> dict[str, Any]:
        # Nothing but the interrupt, and no try/except around it: on resume this whole node runs
        # again, and interrupt() then returns the resume value (SPIKE-04).
        decision = interrupt({"action_id": state["action_id"]})
        return {"review": decision}

    # -- failing closed --

    def guarded(
        self, name: str, node: Callable[[WriteState], Awaitable[dict[str, Any]]]
    ) -> Callable[[WriteState], Awaitable[dict[str, Any]]]:
        async def run(state: WriteState) -> dict[str, Any]:
            started = time.perf_counter()
            with structlog.contextvars.bound_contextvars(action_id=state["action_id"]):
                try:
                    update_ = await node(state)
                except (GraphBubbleUp, asyncio.CancelledError, CommitOutcomeUnknownError):
                    # Not errors: the interrupt machinery, shutdown, and a commit that may have
                    # happened (the action stays `committing`, the checkpoint before `commit`).
                    raise
                except Exception as exc:
                    return await self._fail(state, name, exc, started)
            elapsed = round((time.perf_counter() - started) * 1000)
            if "outcome" in update_:
                return update_
            return {**update_, "work_ms": state.get("work_ms", 0) + elapsed}

        return run

    async def _fail(
        self, state: WriteState, node: str, exc: Exception, started: float
    ) -> dict[str, Any]:
        if isinstance(exc, ShadowBusyError | ShadowFailedError | FailClosed):
            reason = exc.reason_code
        else:
            reason = "workflow_error"
        # The class only: an exception message can hold SQL, row values or a connection string.
        logger.error("fail_closed", reason_code=reason, node=node, error_class=type(exc).__name__)
        action_id = self._id(state)
        async with self._tx() as session:
            action = await session.get(Action, action_id, with_for_update=True)
            if action is not None and (
                ActionStatus.FAILED in ALLOWED_TRANSITIONS[ActionStatus(action.status)]
            ):
                await set_status(
                    session,
                    action_id,
                    ActionStatus.FAILED,
                    failure_reason=reason,
                    total_ms=self._total_ms(state, started),
                    decision=Decision.BLOCK,
                    data={"reason": reason},
                )
            if action is not None:
                await add_event(
                    session,
                    action_id,
                    AuditEventType.FAIL_CLOSED,
                    {"reason": reason, "node": node, "error_class": type(exc).__name__},
                )
        return {"outcome": "failed", "work_ms": self._total_ms(state, started)}


def build_graph(
    deps: WorkflowDeps, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    nodes = _Nodes(deps)
    builder = StateGraph(WriteState)
    for name in (
        "classify",
        "pre_rules",
        "fast_lane",
        "shadow",
        "post_rules",
        "retrieve",
        "impact",
        "judge",
        "decide",
        "approve",
        "request_review",
        "block",
        "commit",
    ):
        builder.add_node(name, nodes.guarded(name, getattr(nodes, name)))
    # The review node is not guarded: GraphInterrupt must pass through it untouched.
    builder.add_node(REVIEW_NODE, nodes.human_review)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", next_unless_ended("pre_rules"))
    builder.add_conditional_edges("pre_rules", route_after_pre_rules)
    builder.add_conditional_edges("fast_lane", next_unless_ended("approve"))
    builder.add_conditional_edges("shadow", next_unless_ended("post_rules"))
    builder.add_conditional_edges("post_rules", route_after_post_rules)
    builder.add_conditional_edges("retrieve", next_unless_ended("impact"))
    builder.add_conditional_edges("impact", next_unless_ended("judge"))
    builder.add_conditional_edges("judge", next_unless_ended("decide"))
    builder.add_conditional_edges("decide", route_after_decide)
    builder.add_conditional_edges("approve", next_unless_ended("commit"))
    builder.add_conditional_edges("request_review", next_unless_ended(REVIEW_NODE))
    builder.add_conditional_edges(REVIEW_NODE, route_after_human_review)
    builder.add_edge("block", END)
    builder.add_conditional_edges("commit", route_after_commit)
    return builder.compile(checkpointer=checkpointer)


# --- Running (step 15 wraps these) --------------------------------------------------------------


async def run_write(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    action_id: uuid.UUID,
    agent_id: uuid.UUID,
    env: str,
) -> Any:
    """Start a write's run. `durability="sync"` stores each step's checkpoint before the next step
    starts: with the default, a crash loses the last steps and the thread has no checkpoint at all
    (SPIKE-04, D-176)."""
    return await graph.ainvoke(
        {"action_id": str(action_id)},
        run_config(action_id, agent_id, env),
        durability="sync",
        version="v2",
    )


async def resume_write(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    action_id: uuid.UUID,
    agent_id: uuid.UUID,
    env: str,
    approved: bool,
) -> Any:
    """Resume a thread paused at `human_review`. LangGraph does not stop a double resume (D-177):
    step 15's SQL claim does, before this is called. A thread in any other state is refused."""
    config = run_config(action_id, agent_id, env)
    snapshot = await graph.aget_state(config)
    if snapshot.next != (REVIEW_NODE,):
        raise NotAwaitingReviewError("the thread is not waiting for a review")
    return await graph.ainvoke(
        Command(resume={"approved": approved}), config, durability="sync", version="v2"
    )
