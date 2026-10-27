"""Demo actions for local development (backend plan step 20, `seed-demo-actions`, BE-121).

Creates two demo users with an agent each, then runs real writes for both through the real graph
and runner: the shadow run is real (`ShadowRunner` on `FakeNeonClient` against the Compose
`company-db`), retrieval is real, the two models are scripted (`scripted_models`) and the commit is
the stub. The pending actions are therefore paused at a real `interrupt()` and can be approved or
rejected from `/docs`. Nothing here touches Neon or `company_main`.

Fail closed: production, a missing URL, a system with another profile, an admin or inactive demo
user, or a scenario that does not end in its expected status all stop the seed or are reported.
No SQL, row, URL, password or key reaches a log or the report.
"""

import dataclasses
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.db import agents as agents_db
from app.db import systems as systems_db
from app.db import users as users_db
from app.db.actions import create_action, expire_if_overdue
from app.db.enums import ActionStatus, UserRole
from app.db.models import Action, Agent, KnowledgeChunk, System, User
from app.db.session import make_engine, make_sessionmaker
from app.services import auth as auth_service
from app.services.commit import StubCommitter
from app.services.embeddings import Embedder, build_embedder
from app.services.impact import ImpactEngine, ImpactReport
from app.services.judge import JudgeVerdict, LLMJudge
from app.services.knowledge import KnowledgeService
from app.services.neon import neon_client_for
from app.services.policy import load_policy
from app.services.scripted_models import ScriptedModelFactory
from app.services.shadow import ShadowRunner
from app.services.vectorstores import VectorStore, build_vector_store
from app.workflow.checkpointer import make_checkpointer, make_checkpointer_pool
from app.workflow.graph import WorkflowDeps, build_graph
from app.workflow.runner import Decider, WorkflowRunner

logger = structlog.get_logger()

DEMO_USERS = (
    ("demo-user-1@example.com", "Dana Demo"),
    ("demo-user-2@example.com", "Sam Sample"),
)
DEMO_AGENT_NAME = "demo-agent"
DEMO_SYSTEM_NAME = "Demo seed (local)"
# The expired scenarios run on a graph whose clock is this far behind, so `request_review` sets an
# `expires_at` that is already past.
PAST_MARGIN = timedelta(hours=1)

Then = Literal["none", "approve", "reject", "expire"]


class SeedError(Exception):
    """The seed cannot run or did not finish; the message is safe to show (no URL, SQL or key)."""


@dataclass(frozen=True)
class SeedScenario:
    key: str
    owner: Literal[1, 2]
    sql: str
    intent: str
    expect: ActionStatus
    then: Then = "none"
    summary: ImpactReport | None = None  # scripted wording, no digits; None uses the default
    verdict: JudgeVerdict | None = None
    reject_note: str | None = None


@dataclass(frozen=True)
class SeededAction:
    key: str
    owner_email: str
    action_id: uuid.UUID
    status: str
    expected: str

    @property
    def ok(self) -> bool:
        return self.status == self.expected


@dataclass(frozen=True)
class SeedReport:
    actions: list[SeededAction]
    knowledge_chunks: int

    @property
    def ok(self) -> bool:
        return all(a.ok for a in self.actions)


def _report(headline: str, point: str, concern: str) -> ImpactReport:
    return ImpactReport(headline=headline, key_points=[point], concerns=[concern])


def _verdict(
    recommendation: Literal["allow", "review", "block"],
    risk: Literal["low", "medium", "high", "critical"],
    reason: str,
    confidence: float = 0.85,
) -> JudgeVerdict:
    return JudgeVerdict(
        recommendation=recommendation, risk_level=risk, confidence=confidence, reason=reason
    )


_S = ActionStatus

_REPLIT_SQL = "DELETE FROM users WHERE last_login_at < now() - interval '6 months'"
_FREE_USERS_SQL = (
    "DELETE FROM users WHERE last_login_at < now() - interval '6 months' AND plan = 'free'"
)
_CLOSED_TICKETS_SQL = (
    "DELETE FROM support_tickets WHERE status = 'closed' AND closed_at < now() - interval '1 year'"
)
_UPGRADE_ORG_SQL = (
    "UPDATE users SET plan = 'pro' FROM organizations o"
    " WHERE users.organization_id = o.id AND o.id = 7"
)
_DOWNGRADE_SQL = "UPDATE users SET plan = 'free' WHERE id = 105"
_CANCELLED_ORDERS_SQL = "DELETE FROM orders WHERE status = 'cancelled'"

_REVIEW_REPORT = _report(
    "Removes rows from a customer table and the rows that depend on them.",
    "The measured counts are in the impact table above.",
    "Rows that depend on the removed ones are deleted too.",
)
_REVIEW_VERDICT = _verdict(
    "review", "high", "A person should confirm this change before it is applied."
)
_ALLOW_VERDICT = _verdict("allow", "low", "A small, routine change.", confidence=0.95)
_ALLOW_REPORT = _report(
    "Adds or changes a few rows in a support table.",
    "Only the rows named in the statement change.",
    "Nothing in the preview looks risky.",
)

SCENARIOS: tuple[SeedScenario, ...] = (
    # --- Demo user 1 ---
    SeedScenario(
        "u1-pending-replit",
        1,
        _REPLIT_SQL,
        "Clean up inactive users",
        _S.AWAITING_REVIEW,
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario(
        "u1-rejected-closed-tickets",
        1,
        _CLOSED_TICKETS_SQL,
        "Purge old closed support tickets",
        _S.REJECTED,
        then="reject",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
        reject_note="Keep closed tickets for the audit period.",
    ),
    SeedScenario(
        "u1-committed-human",
        1,
        _UPGRADE_ORG_SQL,
        "Upgrade the partner organisation to the pro plan",
        _S.COMMITTED,
        then="approve",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario(
        "u1-committed-auto",
        1,
        "INSERT INTO customer_notes (user_id, body)"
        " VALUES (42, 'Called about renewal; will upgrade next month.')",
        "Log the renewal call",
        _S.COMMITTED,
    ),
    SeedScenario(
        "u1-expired",
        1,
        _FREE_USERS_SQL,
        "Remove inactive free accounts",
        _S.EXPIRED,
        then="expire",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario("u1-blocked-drop", 1, "DROP TABLE users CASCADE", "Reset the users", _S.BLOCKED),
    SeedScenario(
        "u1-failed-unknown-table",
        1,
        "UPDATE no_such_table SET x = 1 WHERE id = 1",
        "Fix a value",
        _S.FAILED,
    ),
    # --- Demo user 2 ---
    SeedScenario(
        "u2-pending-orders",
        2,
        _CANCELLED_ORDERS_SQL,
        "Clear cancelled orders",
        _S.AWAITING_REVIEW,
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario(
        "u2-rejected-downgrade",
        2,
        _DOWNGRADE_SQL,
        "Downgrade a customer to the free plan",
        _S.REJECTED,
        then="reject",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
        reject_note="The customer still has an active contract.",
    ),
    SeedScenario(
        "u2-committed-human",
        2,
        _CLOSED_TICKETS_SQL,
        "Purge old closed support tickets",
        _S.COMMITTED,
        then="approve",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario(
        "u2-committed-auto",
        2,
        "INSERT INTO support_tickets (user_id, subject, status, priority)"
        " VALUES (42, 'Cannot export CSV', 'open', 'normal')",
        "Log a new support ticket",
        _S.COMMITTED,
        summary=_ALLOW_REPORT,
        verdict=_ALLOW_VERDICT,
    ),
    SeedScenario(
        "u2-expired",
        2,
        _UPGRADE_ORG_SQL,
        "Upgrade the partner organisation to the pro plan",
        _S.EXPIRED,
        then="expire",
        summary=_REVIEW_REPORT,
        verdict=_REVIEW_VERDICT,
    ),
    SeedScenario(
        "u2-blocked-prices",
        2,
        "UPDATE products SET price_cents = price_cents * 2",
        "Raise every price",
        _S.BLOCKED,
    ),
    SeedScenario(
        "u2-failed-unknown-table",
        2,
        "UPDATE no_such_table SET x = 1 WHERE id = 1",
        "Fix a value",
        _S.FAILED,
    ),
)

ADD_PENDING = SeedScenario(
    "u1-pending-extra",
    1,
    _DOWNGRADE_SQL,
    "Downgrade a customer to the free plan",
    _S.AWAITING_REVIEW,
    summary=_REVIEW_REPORT,
    verdict=_REVIEW_VERDICT,
)


class _ShiftedClock:
    def __init__(self, base: Clock, delta: timedelta) -> None:
        self._base = base
        self._delta = delta

    def now(self) -> datetime:
        return self._base.now() + self._delta


def _check_settings(settings: Settings) -> None:
    if settings.app_env == "production":
        raise SeedError("The demo seed never runs with APP_ENV=production.")
    missing = [
        name
        for name, value in (
            ("BLASTPROOF_DATABASE_URL", settings.blastproof_database_url),
            ("CHECKPOINTER_DATABASE_URL", settings.checkpointer_database_url),
            ("LOCAL_COMPANY_WRITER_URL", settings.local_company_writer_url),
            ("FAKE_NEON_ADMIN_URL", settings.fake_neon_admin_url),
        )
        if value is None
    ]
    if missing:
        raise SeedError(
            f"The demo seed needs {', '.join(missing)} (see backend/.env.example for local values)."
        )


@dataclass(frozen=True)
class _Accounts:
    users: dict[int, User]
    agents: dict[int, Agent]
    system: System


async def _ensure_accounts(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    password: Callable[[], str] | None,
    *,
    create: bool,
) -> _Accounts:
    """Get or create the demo users, their agents and the system. With `create` false (the
    `--add-pending` run) anything missing is an error: the seed has not run yet."""
    users: dict[int, User] = {}
    agents: dict[int, Agent] = {}
    secret: str | None = None
    for index, (email, name) in enumerate(DEMO_USERS, start=1):
        async with sessionmaker() as session:
            user = await users_db.get_user_by_email(session, email)
        if user is None:
            if not create:
                raise SeedError("The demo seed has not run yet: run seed-demo-actions first.")
            if password is None:
                raise SeedError("The demo users must be created, which needs a password.")
            secret = secret or password()
            async with sessionmaker() as session:
                user = await auth_service.create_user(
                    session,
                    email=email,
                    display_name=name,
                    role=UserRole.MEMBER,
                    password=secret,
                )
        elif user.role != UserRole.MEMBER.value or not user.is_active:
            raise SeedError(f"{email} exists but is not an active member; refusing to use it.")
        users[index] = user
        async with sessionmaker() as session:
            agent = await agents_db.get_agent_by_owner_and_name(session, user.id, DEMO_AGENT_NAME)
        if agent is None:
            if not create:
                raise SeedError("The demo seed has not run yet: run seed-demo-actions first.")
            # The key is dropped: demo agents never connect, they only own the actions.
            async with sessionmaker() as session:
                agent = (
                    await auth_service.create_agent(
                        session, owner_email=email, name=DEMO_AGENT_NAME
                    )
                ).agent
        agents[index] = agent
    async with sessionmaker.begin() as session:
        system = await systems_db.get_or_create_system(
            session,
            name=DEMO_SYSTEM_NAME,
            connection_profile="local",
            neon_project_id=None,
            neon_parent_branch_id=None,
        )
        if system.connection_profile != "local":
            raise SeedError(f"The system {DEMO_SYSTEM_NAME!r} exists with another profile.")
    return _Accounts(users, agents, system)


async def _status(sessionmaker: async_sessionmaker[AsyncSession], action_id: uuid.UUID) -> str:
    async with sessionmaker() as session:
        return (
            await session.execute(select(Action.status).where(Action.id == action_id))
        ).scalar_one()


async def seed_demo_actions(
    settings: Settings,
    *,
    password: Callable[[], str] | None,
    add_pending: bool = False,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    clock: Clock | None = None,
) -> SeedReport:
    """Run the scenarios (or just `--add-pending`'s one) and report where each action ended.

    `password` is called at most once, and only when a demo user has to be created.
    """
    _check_settings(settings)
    clock = clock or SystemClock()
    assert settings.blastproof_database_url and settings.checkpointer_database_url
    neon = neon_client_for("local", settings, clock)
    if neon is None:
        raise SeedError("The local company database is not configured.")
    engine = make_engine(settings.blastproof_database_url.get_secret_value())
    pool = make_checkpointer_pool(settings.checkpointer_database_url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        accounts = await _ensure_accounts(sessionmaker, settings, password, create=not add_pending)
        await pool.open(wait=True)

        policy = load_policy(settings.policy_file)
        models = ScriptedModelFactory()
        store = store or build_vector_store(settings, sessionmaker)
        assert store is not None
        deps = WorkflowDeps(
            sessionmaker=sessionmaker,
            policy=policy,
            shadow=ShadowRunner(
                policy=policy,
                clients={"local": neon},
                max_concurrent=settings.max_concurrent_shadows,
                timeout_seconds=settings.shadow_timeout_seconds,
            ),
            impact=ImpactEngine(settings, policy, model_factory=models),
            judge=LLMJudge(settings, policy, model_factory=models),
            knowledge=KnowledgeService(sessionmaker, embedder or build_embedder(settings), store),
            committer=StubCommitter(),
            clock=clock,
            settings=settings,
        )
        checkpointer = make_checkpointer(pool)
        # Recovery and the sweep are never run here: they would act on the dev server's own actions.
        now_runner = WorkflowRunner(build_graph(deps, checkpointer), sessionmaker, clock, settings)
        past_clock = _ShiftedClock(
            clock, -(timedelta(hours=settings.review_timeout_hours) + PAST_MARGIN)
        )
        past_runner = WorkflowRunner(
            build_graph(dataclasses.replace(deps, clock=past_clock), checkpointer),
            sessionmaker,
            clock,
            settings,
        )
        now_runner.mark_recovered()
        past_runner.mark_recovered()

        seeded: list[SeededAction] = []
        for scenario in (ADD_PENDING,) if add_pending else SCENARIOS:
            seeded.append(
                await _run_scenario(
                    scenario, sessionmaker, accounts, models, now_runner, past_runner, clock
                )
            )
        async with sessionmaker() as session:
            chunks = (
                await session.execute(select(func.count()).select_from(KnowledgeChunk))
            ).scalar_one()
        return SeedReport(seeded, chunks)
    finally:
        await pool.close()
        await neon.aclose()
        await engine.dispose()


async def _run_scenario(
    scenario: SeedScenario,
    sessionmaker: async_sessionmaker[AsyncSession],
    accounts: _Accounts,
    models: ScriptedModelFactory,
    runner: WorkflowRunner,
    past_runner: WorkflowRunner,
    clock: Clock,
) -> SeededAction:
    user, agent = accounts.users[scenario.owner], accounts.agents[scenario.owner]
    async with sessionmaker.begin() as session:
        action = await create_action(
            session,
            user_id=user.id,
            agent_id=agent.id,
            system_id=accounts.system.id,
            tool="query_database",
            intent=scenario.intent,
            sql=scenario.sql,
        )
        action_id = action.id
    models.script(action_id, report=scenario.summary, verdict=scenario.verdict)

    active = past_runner if scenario.then == "expire" else runner
    active.start(action_id, agent.id)
    await active.join()

    if scenario.then in ("approve", "reject") and (
        await _status(sessionmaker, action_id) == ActionStatus.AWAITING_REVIEW.value
    ):
        approved = scenario.then == "approve"
        await runner.resume(
            action_id,
            approved=approved,
            note=None if approved else scenario.reject_note,
            decider=Decider(user.id, user.display_name, False),
        )
        await runner.join()
    elif scenario.then == "expire":
        async with sessionmaker.begin() as session:
            await expire_if_overdue(session, action_id, clock.now())

    status = await _status(sessionmaker, action_id)
    logger.info("seed_action", key=scenario.key, status=status, action_id=str(action_id))
    return SeededAction(scenario.key, user.email, action_id, status, scenario.expect.value)
