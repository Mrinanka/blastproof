"""`--mode live`: replay the scenarios through the real pipeline with the configured models.

The wiring is `seed-demo-actions`' (`app/services/demo_seed.py`): the real graph and runner,
`ShadowRunner` on `FakeNeonClient` against the Compose `company-db`, real retrieval, the real impact
engine and judge. The commit is the stub, so `company_main` never changes. The clock is frozen at
the scenario anchor date, so `now()` in the SQL pins to the date `expected_impact` was measured at.

Writes go through `runner.start` (not `query_database`), so the per-agent rate limit never applies.
Their latency is Blastproof's own work time from the graph state: classify to decision, without the
real commit or the MCP layer. Reads go through `query_database` and are timed against a plain reader
connection; the difference is Blastproof's overhead.

Nothing here touches Neon. Every refusal message is safe to print (no URL, SQL or key).
"""

import asyncio
import dataclasses
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import asyncpg
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.auth import CurrentAgent
from app.core.clock import FrozenClock, SystemClock
from app.db import agents as agents_db
from app.db import systems as systems_db
from app.db import users as users_db
from app.db.actions import create_action
from app.db.enums import ActionStatus, UserRole
from app.db.models import Action, Agent, KnowledgeChunk, System, User
from app.db.session import make_engine, make_sessionmaker
from app.services import agent_calls, company_db, reads
from app.services import auth as auth_service
from app.services.commit import StubCommitter
from app.services.embeddings import Embedder, build_embedder
from app.services.impact import ImpactEngine
from app.services.judge import LLMJudge
from app.services.knowledge import KnowledgeService, Retrieval
from app.services.llm import ModelFactory, build_chat_model
from app.services.neon import neon_client_for
from app.services.policy import load_policy
from app.services.shadow import ShadowRunner
from app.services.systems import SystemProfileMismatchError
from app.services.vectorstores import VectorStore, build_vector_store
from app.workflow.checkpointer import make_checkpointer, make_checkpointer_pool
from app.workflow.graph import WorkflowDeps, build_graph, run_config
from app.workflow.runner import WorkflowRunner
from evals.metrics import Outcome, PathKind
from evals.reads import READ_QUERIES
from evals.rules_mode import EvalError, anchor_time, error_text, hook_outcome
from evals.schema import SqlScenario, load_scenario_file

logger = structlog.get_logger()

EVAL_EMAIL = "evals@example.com"
EVAL_NAME = "Eval runner"
EVAL_AGENT = "evals"
EVAL_SYSTEM = "Evals (local)"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_READS_REPEAT = 20

JudgeVariant = Literal["rag", "no-rag"]
_PATHS: dict[str, PathKind] = {"block": "blocked", "fast_lane": "fast_lane", "preview": "previewed"}


class LiveModeError(EvalError):
    """The live eval cannot run; the message is safe to show."""


PROFILE_MISMATCH_TEXT = (
    "reads are measured through the main system, which this Blastproof database stores with another"
    " profile than COMPANY_CONNECTION_PROFILE=local; use a database where it is local (a fresh"
    " Compose volume, as the nightly workflow does)"
)


class EmptyKnowledge:
    """Retrieval that finds nothing: the judge still runs, without business rules (the "no RAG"
    variant). `knowledge=None` would make the graph skip the judge instead."""

    async def retrieve(self, intent: str, tables: Sequence[str], k: int = 0) -> Retrieval:
        return Retrieval(chunks=[], refs=[], duration_ms=0)


def check_live_settings(settings: Settings) -> None:
    """Refuse before anything connects: production, a Neon profile, or a missing local URL."""
    if settings.app_env == "production":
        raise LiveModeError("The live eval never runs with APP_ENV=production.")
    if settings.company_connection_profile != "local" or settings.neon_client != "fake":
        raise LiveModeError(
            "The live eval runs only on the local profile with the fake Neon client"
            " (COMPANY_CONNECTION_PROFILE=local, NEON_CLIENT=fake)."
        )
    missing = [
        name
        for name, value in (
            ("BLASTPROOF_DATABASE_URL", settings.blastproof_database_url),
            ("CHECKPOINTER_DATABASE_URL", settings.checkpointer_database_url),
            ("LOCAL_COMPANY_READER_URL", settings.local_company_reader_url),
            ("LOCAL_COMPANY_WRITER_URL", settings.local_company_writer_url),
            ("FAKE_NEON_ADMIN_URL", settings.fake_neon_admin_url),
        )
        if value is None
    ]
    if missing:
        raise LiveModeError(
            f"The live eval needs {', '.join(missing)} (see backend/.env.example for local values)."
        )


def _admin_company_url(settings: Settings) -> str:
    """The fake-Neon admin URL pointed at the company database the reader URL names."""
    assert settings.fake_neon_admin_url and settings.local_company_reader_url
    database = urlsplit(settings.local_company_reader_url.get_secret_value()).path
    admin = urlsplit(settings.fake_neon_admin_url.get_secret_value())
    return admin._replace(path=database).geturl()


async def check_live_data(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], anchor: date
) -> None:
    """The seed must be at the scenarios' anchor date, and the knowledge base must be filled."""
    try:
        conn = await asyncpg.connect(_admin_company_url(settings), timeout=10)
    except (OSError, TimeoutError, asyncpg.PostgresError):
        raise LiveModeError("The company database is not reachable (is Docker up?).") from None
    try:
        seeded = await conn.fetchval("SELECT anchor_date FROM blastproof.seed_info")
    except asyncpg.PostgresError:
        raise LiveModeError(
            "The company database has no seed (run demo_company/seed.py)."
        ) from None
    finally:
        await conn.close()
    if seeded != anchor:
        raise LiveModeError(
            f"The seed's anchor date is {seeded}, the scenarios use {anchor}: re-seed the"
            " company database (rebase-dates moves it away from the labels)."
        )
    async with sessionmaker() as session:
        chunks = (
            await session.execute(select(func.count()).select_from(KnowledgeChunk))
        ).scalar_one()
    if chunks == 0:
        raise LiveModeError(
            "There are no knowledge chunks: run knowledge-scan and knowledge-ingest first."
        )


@dataclass(frozen=True)
class EvalAccounts:
    user: User
    agent: Agent
    system: System


async def ensure_eval_accounts(sessionmaker: async_sessionmaker[AsyncSession]) -> EvalAccounts:
    """The member, agent and local system the eval's actions belong to. The password is random and
    dropped; an existing account that is not an active member is refused."""
    async with sessionmaker() as session:
        user = await users_db.get_user_by_email(session, EVAL_EMAIL)
    if user is None:
        async with sessionmaker() as session:
            user = await auth_service.create_user(
                session,
                email=EVAL_EMAIL,
                display_name=EVAL_NAME,
                role=UserRole.MEMBER,
                password=secrets.token_urlsafe(32),
            )
    elif user.role != UserRole.MEMBER.value or not user.is_active:
        raise LiveModeError(f"{EVAL_EMAIL} exists but is not an active member; refusing to use it.")
    async with sessionmaker() as session:
        agent = await agents_db.get_agent_by_owner_and_name(session, user.id, EVAL_AGENT)
    if agent is None:
        # The key is dropped: the eval never connects as the agent.
        async with sessionmaker() as session:
            agent = (
                await auth_service.create_agent(session, owner_email=EVAL_EMAIL, name=EVAL_AGENT)
            ).agent
    async with sessionmaker.begin() as session:
        system = await systems_db.get_or_create_system(
            session,
            name=EVAL_SYSTEM,
            connection_profile="local",
            neon_project_id=None,
            neon_parent_branch_id=None,
        )
        if system.connection_profile != "local":
            raise LiveModeError(f"The system {EVAL_SYSTEM!r} exists with another profile.")
    return EvalAccounts(user, agent, system)


def _nonzero(changes: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {t: {k: v for k, v in c.items() if v} for t, c in changes.items() if any(c.values())}


def facts_equal(scenario: SqlScenario, impact: dict[str, Any] | None) -> bool | None:
    """Do the measured facts equal the labelled `expected_impact`? None when there is nothing to
    compare (no label, or no preview ran)."""
    expected = scenario.expected_impact
    if expected is None or not impact:
        return None
    want = _nonzero({t: c.model_dump() for t, c in expected.table_changes.items()})
    if _nonzero(impact.get("table_changes", {})) != want:
        return False
    breakdowns = impact.get("breakdowns", {})
    if any(breakdowns.get(k) != v for k, v in expected.breakdowns.items()):
        return False
    derived = impact.get("derived_groups", {})
    return all(derived.get(k) == v for k, v in expected.derived_groups.items())


async def _outcome_for(
    scenario: SqlScenario,
    file: str,
    action_id: uuid.UUID,
    agent_id: uuid.UUID,
    sessionmaker: async_sessionmaker[AsyncSession],
    graph: Any,
    settings: Settings,
) -> Outcome:
    async with sessionmaker() as session:
        action = await session.get(Action, action_id)
    assert action is not None
    snapshot = await graph.aget_state(run_config(action_id, agent_id, settings.app_env))
    state: dict[str, Any] = dict(snapshot.values)
    ms = float(state["work_ms"]) if "work_ms" in state else None
    cost = float(action.llm_cost_usd) if action.llm_cost_usd is not None else None
    common: dict[str, Any] = {
        "id": scenario.id,
        "file": file,
        "kind": "sql",
        "expected": scenario.expected_decision,
        "incident": scenario.incident is not None,
        "path": _PATHS.get(str(state.get("route"))),
        "reason": action.policy_reason or "",
        "ms": ms,
        "ai_unavailable": bool(state.get("ai_unavailable", False)),
        "cost_usd": cost,
    }
    if action.status == ActionStatus.FAILED.value:
        return Outcome(**common, actual=None, error=f"failed: {action.failure_reason}")
    report = action.impact_report or {}
    return Outcome(
        **common,
        actual=action.decision,
        summary_source=report.get("source"),
        fallback_reason=report.get("fallback_reason"),
        facts_match=facts_equal(scenario, action.impact),
    )


@dataclass
class LivePipeline:
    """The wired pipeline, ready to run scenarios one at a time."""

    settings: Settings
    accounts: EvalAccounts
    sessionmaker: async_sessionmaker[AsyncSession]
    runner: WorkflowRunner
    graph: Any

    async def run(
        self, scenario: SqlScenario, file: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> Outcome:
        failed = Outcome(
            id=scenario.id,
            file=file,
            kind="sql",
            expected=scenario.expected_decision,
            actual=None,
            incident=scenario.incident is not None,
        )
        try:
            async with self.sessionmaker.begin() as session:
                action = await create_action(
                    session,
                    user_id=self.accounts.user.id,
                    agent_id=self.accounts.agent.id,
                    system_id=self.accounts.system.id,
                    tool="query_database",
                    intent=scenario.intent,
                    sql=scenario.sql,
                )
                action_id = action.id
            self.runner.start(action_id, self.accounts.agent.id)
            try:
                await asyncio.wait_for(self.runner.join(), timeout_seconds)
            except TimeoutError:
                logger.warning("eval_scenario_timeout", scenario=scenario.id)
                return dataclasses.replace(failed, error="timeout")
            return await _outcome_for(
                scenario,
                file,
                action_id,
                self.accounts.agent.id,
                self.sessionmaker,
                self.graph,
                self.settings,
            )
        except Exception as exc:
            logger.error(
                "eval_scenario_error", scenario=scenario.id, error_class=type(exc).__name__
            )
            return dataclasses.replace(failed, error=error_text(exc))


@asynccontextmanager
async def live_pipeline(
    settings: Settings,
    anchor: date,
    *,
    judge_variant: JudgeVariant = "rag",
    model_factory: ModelFactory = build_chat_model,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> AsyncIterator[LivePipeline]:
    """Check the settings and the data, then wire the pipeline; everything is closed on exit."""
    check_live_settings(settings)
    assert settings.blastproof_database_url and settings.checkpointer_database_url
    clock = FrozenClock(anchor_time(anchor))
    neon = neon_client_for("local", settings, clock)
    if neon is None:
        raise LiveModeError("The local company database is not configured.")
    engine = make_engine(settings.blastproof_database_url.get_secret_value())
    pool = make_checkpointer_pool(settings.checkpointer_database_url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        await check_live_data(settings, sessionmaker, anchor)
        accounts = await ensure_eval_accounts(sessionmaker)
        await pool.open(wait=True)
        policy = load_policy(settings.policy_file)
        knowledge: Any
        if judge_variant == "rag":
            knowledge = KnowledgeService(
                sessionmaker,
                embedder or build_embedder(settings),
                store or build_vector_store(settings, sessionmaker),
            )
        else:
            knowledge = EmptyKnowledge()
        deps = WorkflowDeps(
            sessionmaker=sessionmaker,
            policy=policy,
            shadow=ShadowRunner(
                policy=policy,
                clients={"local": neon},
                max_concurrent=settings.max_concurrent_shadows,
                timeout_seconds=settings.shadow_timeout_seconds,
            ),
            impact=ImpactEngine(settings, policy, model_factory=model_factory),
            judge=LLMJudge(settings, policy, model_factory=model_factory),
            knowledge=knowledge,
            committer=StubCommitter(),
            clock=clock,
            settings=settings,
        )
        graph = build_graph(deps, make_checkpointer(pool))
        # Recovery and the sweep never run here: they would act on the dev server's actions.
        runner = WorkflowRunner(graph, sessionmaker, clock, settings)
        runner.mark_recovered()
        yield LivePipeline(settings, accounts, sessionmaker, runner, graph)
    finally:
        await pool.close()
        await neon.aclose()
        await engine.dispose()


async def run_live(
    settings: Settings,
    scenario_files: Sequence[Path],
    *,
    judge_variant: JudgeVariant = "rag",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    model_factory: ModelFactory = build_chat_model,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    only: frozenset[str] | None = None,
) -> list[Outcome]:
    """One outcome per scenario, in file order. `only` limits the run to those scenario ids."""
    check_live_settings(settings)
    files = [(path, load_scenario_file(path)) for path in scenario_files]
    anchors = {loaded.anchor_date for _, loaded in files}
    if len(anchors) != 1:
        raise LiveModeError("The scenario files do not share one anchor date.")
    (anchor,) = anchors
    outcomes: list[Outcome] = []
    async with live_pipeline(
        settings,
        anchor,
        judge_variant=judge_variant,
        model_factory=model_factory,
        embedder=embedder,
        store=store,
    ) as pipeline:
        for path, loaded in files:
            for scenario in loaded.scenarios:
                if only is not None and scenario.id not in only:
                    continue
                if isinstance(scenario, SqlScenario):
                    outcomes.append(await pipeline.run(scenario, path.name, timeout_seconds))
                else:
                    outcomes.append(hook_outcome(scenario, path.name))
    return outcomes


def _read_outcome(name: str, **values: Any) -> Outcome:
    return Outcome(id=name, file="reads", kind="read", expected=None, actual=None, **values)


async def measure_reads(settings: Settings, *, repeat: int = DEFAULT_READS_REPEAT) -> list[Outcome]:
    """Blastproof's overhead on a read: `query_database` end to end minus a plain reader
    connection running the same statement. One outcome per repetition (clamped at 0: a negative
    difference is noise), after one untimed warm-up of each."""
    check_live_settings(settings)
    assert settings.blastproof_database_url
    engine = make_engine(settings.blastproof_database_url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        accounts = await ensure_eval_accounts(sessionmaker)
        ctx = agent_calls.CallContext(
            settings=settings,
            sessionmaker=sessionmaker,
            agent=CurrentAgent(accounts.agent.id, accounts.agent.name, accounts.user.id),
            clock=SystemClock(),
            runner=lambda: None,
        )

        async def plain(sql: str) -> float:
            started = time.perf_counter()
            async with company_db.connect_reader(settings) as conn:
                await reads.run_read(conn, sql)
            return (time.perf_counter() - started) * 1000

        async def through_blastproof(sql: str) -> tuple[float, str]:
            started = time.perf_counter()
            result = await agent_calls.query_database(ctx, sql, "Eval read overhead")
            return (time.perf_counter() - started) * 1000, result.status

        outcomes: list[Outcome] = []
        try:
            await through_blastproof(next(iter(READ_QUERIES.values())))
        except SystemProfileMismatchError:
            return [_read_outcome("reads", error=PROFILE_MISMATCH_TEXT)]
        for name, sql in READ_QUERIES.items():
            try:
                await plain(sql)
                await through_blastproof(sql)
                for i in range(repeat):
                    base = await plain(sql)
                    full, status = await through_blastproof(sql)
                    if status != "executed":
                        raise LiveModeError(f"read {name} ended as {status}")
                    outcomes.append(
                        _read_outcome(
                            f"read-{name}-{i + 1}", path="read_overhead", ms=max(0.0, full - base)
                        )
                    )
            except Exception as exc:
                outcomes.append(_read_outcome(f"read-{name}", error=error_text(exc)))
        return outcomes
    finally:
        await engine.dispose()
