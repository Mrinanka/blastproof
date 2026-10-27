"""Test doubles and a rig for step 14: the real graph on a scratch Blastproof database with the real
`AsyncPostgresSaver`; the shadow run, the knowledge service, both models and the commit are fakes.

Postgres stays real for actions, events and checkpoints, so the tests prove what a run leaves in
the database. `rig()` opens and closes the checkpointer pool inside the test's own event loop.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.db.session import make_engine, make_sessionmaker
from app.services.commit import CommitOutcome
from app.services.judge import Judge
from app.services.knowledge import Retrieval, RetrievalError
from app.services.shadow import Diff, ShadowResult
from app.workflow.checkpointer import make_checkpointer, make_checkpointer_pool
from app.workflow.graph import (
    WorkflowDeps,
    build_graph,
    resume_write,
    run_config,
    run_write,
)
from app.workflow.runner import Decider, WorkflowRunner
from tests.action_helpers import make_system
from tests.conftest import AuthDb, TestUser, asyncpg_url
from tests.impact_helpers import (
    POLICY,
    REPLIT_INTENT,
    REPLIT_SQL,
    FakeLlm,
    engine_with,
    replit_diff,
    report,
)
from tests.judge_helpers import FakeJudgeLlm, judge_with, verdict

FAST_LANE_SQL = "INSERT INTO customer_notes (user_id, body) VALUES (42, 'Called about renewal')"
SMALL_INSERT_SQL = "INSERT INTO products (name, price_cents) VALUES ('Widget', 1999)"
EVERY_ROW_SQL = "DELETE FROM orders WHERE id > 0"
DROP_SQL = "DROP TABLE users"
READ_SQL = "SELECT count(*) FROM users"
PLANTED_EMAIL = "planted.secret@example.com"
INTENT = "Clean up inactive users"
# No digits and no number words: nothing for the number guardrail to object to.
CALM_HEADLINE = "Changes the data as the statement describes."


@dataclass(frozen=True)
class WorkflowEnv:
    db: AuthDb
    user: TestUser
    agent_id: uuid.UUID
    system_id: uuid.UUID


def make_env(db: AuthDb) -> WorkflowEnv:
    user = db.make_user()
    return WorkflowEnv(db, user, db.make_agent(user.id).id, make_system(db))


def new_action(env: WorkflowEnv, sql: str, *, intent: str | None = INTENT) -> uuid.UUID:
    """A `received` action, as step 17 will create it before starting the graph."""
    [(action_id,)] = env.db.execute(
        "INSERT INTO actions (user_id, agent_id, system_id, tool, intent, sql, status, tables)"
        " VALUES (%s, %s, %s, 'query_database', %s, %s, 'received', '{}') RETURNING id",
        (env.user.id, env.agent_id, env.system_id, intent, sql),
    )
    env.db.execute(
        "INSERT INTO audit_events (action_id, event_type) VALUES (%s, 'received')", (action_id,)
    )
    return action_id


def claim(env: WorkflowEnv, action_id: uuid.UUID, *, approved: bool) -> int:
    """Step 15's SQL claim, done by hand: the decision is stored before the graph is resumed.
    Returns the rows claimed (0 when the action is not awaiting review)."""
    status = "approved" if approved else "rejected"
    claimed = env.db.execute(
        "UPDATE actions SET status = %s, decided_by = %s, decided_at = now()"
        " WHERE id = %s AND status = 'awaiting_review' RETURNING id",
        (status, env.user.id, action_id),
    )
    if claimed:
        env.db.execute(
            "INSERT INTO audit_events (action_id, event_type, data) VALUES (%s, %s,"
            " jsonb_build_object('user_id', %s::text))",
            (action_id, status, env.user.id),
        )
    return len(claimed)


class FakeShadowRunner:
    """Returns a prepared `Diff` instead of cloning the company database."""

    def __init__(
        self, diff: Diff | None = None, *, raises: BaseException | None = None, preview_ms: int = 7
    ) -> None:
        self.diff = diff
        self.raises = raises
        self.preview_ms = preview_ms
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, action_id: uuid.UUID, profile: str, classification: Any) -> ShadowResult:
        self.calls.append(
            {"action_id": action_id, "profile": profile, "pinned_sql": classification.pinned_sql}
        )
        if self.raises is not None:
            raise self.raises
        assert self.diff is not None, "this test gave the fake shadow run no diff"
        return ShadowResult(self.diff, self.preview_ms)


class FakeKnowledge:
    """Stands in for `KnowledgeService.retrieve`."""

    def __init__(self, retrieval: Retrieval | None = None, *, fails: bool = False) -> None:
        self.retrieval = retrieval or Retrieval(chunks=[], refs=[], duration_ms=1)
        self.fails = fails
        self.calls: list[tuple[str, Sequence[str]]] = []

    async def retrieve(self, intent: str, tables: Sequence[str], k: int = 10) -> Retrieval:
        self.calls.append((intent, tables))
        if self.fails:
            raise RetrievalError("EmbeddingError")
        return self.retrieval


class RecordingCommitter:
    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.committed: list[uuid.UUID] = []
        self.raises = raises

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        if self.raises is not None:
            raise self.raises
        self.committed.append(action_id)
        return CommitOutcome(rows=None, stub=True)


@dataclass
class Rig:
    env: WorkflowEnv
    graph: CompiledStateGraph[Any, Any, Any, Any]
    deps: WorkflowDeps
    shadow: FakeShadowRunner
    summary_llm: FakeLlm
    judge_llm: FakeJudgeLlm
    knowledge: FakeKnowledge
    committer: RecordingCommitter
    judge: Judge
    settings: Settings
    log: list[Any] = field(default_factory=list)

    async def run(self, action_id: uuid.UUID) -> Any:
        return await run_write(
            self.graph, action_id=action_id, agent_id=self.env.agent_id, env="test"
        )

    async def resume(self, action_id: uuid.UUID, *, approved: bool) -> Any:
        return await resume_write(
            self.graph,
            action_id=action_id,
            agent_id=self.env.agent_id,
            env="test",
            approved=approved,
        )

    async def snapshot(self, action_id: uuid.UUID) -> Any:
        return await self.graph.aget_state(run_config(action_id, self.env.agent_id, "test"))

    async def next_nodes(self, action_id: uuid.UUID) -> tuple[str, ...]:
        return tuple((await self.snapshot(action_id)).next)


def default_judge_llm() -> FakeJudgeLlm:
    return FakeJudgeLlm(parsed=verdict("allow", "low", 0.9, "Looks routine for this table."))


@asynccontextmanager
async def rig(
    env: WorkflowEnv,
    *,
    diff: Diff | None = None,
    shadow: FakeShadowRunner | None = None,
    summary_llm: FakeLlm | None = None,
    judge_llm: FakeJudgeLlm | None = None,
    knowledge: FakeKnowledge | None = None,
    committer: RecordingCommitter | None = None,
    settings: dict[str, Any] | None = None,
    real_shadow: Any = None,
    real_knowledge: Any = None,
    clock: Clock | None = None,
    committer_factory: Callable[[Any, Settings], Any] | None = None,
) -> AsyncIterator[Rig]:
    options = {"app_env": "test"} | (settings or {})
    summary_llm = summary_llm or FakeLlm(parsed=report(CALM_HEADLINE))
    judge_llm = judge_llm or default_judge_llm()
    shadow = shadow or FakeShadowRunner(diff)
    knowledge = knowledge or FakeKnowledge()
    committer = committer or RecordingCommitter()
    config = Settings(_env_file=None, **options)
    engine = make_engine(asyncpg_url(env.db.app_url))
    pool = make_checkpointer_pool(env.db.app_url)
    await pool.open(wait=True)
    try:
        impact, _ = engine_with(summary_llm, **options)
        judge, _ = judge_with(judge_llm, **options)
        sessionmaker = make_sessionmaker(engine)
        if committer_factory is not None:
            committer = committer_factory(sessionmaker, config)
        deps = WorkflowDeps(
            sessionmaker=sessionmaker,
            policy=POLICY,
            shadow=real_shadow or shadow,
            impact=impact,
            judge=judge,
            knowledge=real_knowledge or knowledge,
            committer=committer,
            clock=clock or SystemClock(),
            settings=config,
        )
        graph = build_graph(deps, make_checkpointer(pool))
        yield Rig(
            env, graph, deps, shadow, summary_llm, judge_llm, knowledge, committer, judge, config
        )
    finally:
        await pool.close()
        await engine.dispose()


# --- Reading what a run left behind -------------------------------------------------------------


def action_row(env: WorkflowEnv, action_id: uuid.UUID) -> dict[str, Any]:
    columns = (
        "status, decision, risk_level, policy_reason, failure_reason, total_ms, matched_rules,"
        " judge, impact, impact_report, knowledge_used, decided_by, decided_at, expires_at,"
        " llm_cost_usd, pinned_sql, sql_display, preview_ms, statement_type, tables"
    )
    [row] = env.db.execute(f"SELECT {columns} FROM actions WHERE id = %s", (action_id,))
    return dict(zip(columns.replace(" ", "").split(","), row, strict=True))


def events(env: WorkflowEnv, action_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    rows = env.db.execute(
        "SELECT event_type, data FROM audit_events WHERE action_id = %s ORDER BY created_at, id",
        (action_id,),
    )
    return [(kind, data) for kind, data in rows]


def event_types(env: WorkflowEnv, action_id: uuid.UUID) -> list[str]:
    return [kind for kind, _ in events(env, action_id)]


def checkpoint_count(env: WorkflowEnv, action_id: uuid.UUID) -> int:
    [(count,)] = env.db.execute(
        "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (str(action_id),)
    )
    return count


def checkpoint_text(env: WorkflowEnv, action_id: uuid.UUID) -> str:
    """Everything the graph stored for this thread, as text, for leak searches."""
    thread = (str(action_id),)
    parts = [
        text
        for (text,) in env.db.execute(
            "SELECT checkpoint::text || metadata::text FROM checkpoints WHERE thread_id = %s",
            thread,
        )
    ]
    for table in ("checkpoint_blobs", "checkpoint_writes"):
        parts += [
            text
            for (text,) in env.db.execute(
                f"SELECT encode(blob, 'escape') FROM {table}"
                " WHERE thread_id = %s AND blob IS NOT NULL",
                thread,
            )
        ]
    return "\n".join(parts)


def status_history(env: WorkflowEnv, action_id: uuid.UUID) -> list[str]:
    """The audit events that are status changes, in order."""
    extras = {"shadow_done", "decided", "fail_closed"}
    return [kind for kind in event_types(env, action_id) if kind not in extras]


# --- The runner (step 15) -----------------------------------------------------------------------


def runner_for(r: Rig, *, recovered: bool = True, clock: Clock | None = None) -> WorkflowRunner:
    """A runner on the rig's graph. `recovered` skips startup recovery, which would otherwise act on
    every action the module's other tests left behind."""
    runner = WorkflowRunner(r.graph, r.deps.sessionmaker, clock or r.deps.clock, r.settings)
    if recovered:
        runner.mark_recovered()
    return runner


def decider_for(user: TestUser, *, name: str | None = None, is_admin: bool = False) -> Decider:
    return Decider(user.id, name or f"User {user.email[:10]}", is_admin)


async def paused_action(r: Rig, env: WorkflowEnv | None = None) -> uuid.UUID:
    """A Replit-style delete that ran to `awaiting_review` (paused at `human_review`)."""
    env = env or r.env
    action_id = new_action(env, REPLIT_SQL, intent=REPLIT_INTENT)
    await r.run(action_id)
    assert action_row(env, action_id)["status"] == "awaiting_review"
    return action_id


def paused_rig(env: WorkflowEnv, **options: Any) -> Any:
    """`rig` with the Replit diff, so `paused_action` reaches review."""
    return rig(env, diff=replit_diff(), **options)


# --- Step 16: the real commit on a throwaway company database -----------------------------------


async def no_sleep(_: float) -> None:
    return None


@asynccontextmanager
async def company_rig(
    env: WorkflowEnv,
    company: Any,
    *,
    fault: Any = None,
    sleep: Any = no_sleep,
    clock: Clock | None = None,
    settings: dict[str, Any] | None = None,
    **options: Any,
) -> AsyncIterator[Rig]:
    """The real graph with the real `ShadowRunner` and the real `CompanyCommitter`, both on
    `company` (a `CommitCompany`). The models, retrieval and judge are still fakes."""
    from app.services.committer import CompanyCommitter
    from tests.conftest import shadow_runner

    clock = clock or SystemClock()
    shadow = shadow_runner({"local": company.client(clock)})

    def committer_factory(sessionmaker: Any, config: Settings) -> CompanyCommitter:
        return CompanyCommitter(sessionmaker, config, clock, fault=fault, sleep=sleep)

    values = {"local_company_writer_url": company.writer_url} | (settings or {})
    async with rig(
        env,
        real_shadow=shadow,
        committer_factory=committer_factory,
        settings=values,
        clock=clock,
        **options,
    ) as r:
        yield r


async def review_action(r: Rig, sql: str, *, intent: str = INTENT) -> uuid.UUID:
    """A write that ran through the real preview to `awaiting_review`."""
    action_id = new_action(r.env, sql, intent=intent)
    await r.run(action_id)
    assert action_row(r.env, action_id)["status"] == "awaiting_review"
    return action_id


async def approve(r: Rig, action_id: uuid.UUID) -> Any:
    """Claim the approval as step 15 does, then resume the graph."""
    assert claim(r.env, action_id, approved=True) == 1
    return await r.resume(action_id, approved=True)
