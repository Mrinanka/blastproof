"""`evals.run --mode live` on the real graph, shadow run and retrieval (backend plan §14).

Only the models are fakes (and the embedder, a hashed bag of words). Blastproof's own database is a
scratch copy; the company database is the Compose `company_main`, which the eval must leave alone.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Coroutine, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command as alembic_command
from structlog.testing import capture_logs

import app.workflow.graph as graph_module
from app.config import Settings
from app.db import systems as systems_db
from app.db.session import make_engine, make_sessionmaker
from app.services.knowledge import KnowledgeService
from app.services.systems import MAIN_SYSTEM_NAME
from app.services.vectorstores.memory import MemoryVectorStore
from evals import live_mode, run
from evals.live_mode import (
    EVAL_EMAIL,
    LiveModeError,
    ensure_eval_accounts,
    measure_reads,
    run_live,
)
from evals.metrics import Outcome, gate_failures, summarize
from evals.reads import READ_QUERIES
from evals.report import write_json
from tests.conftest import (
    BLASTPROOF_OWNER_URL,
    COMPANY_ADMIN_URL,
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    SCENARIOS_DIR,
    AuthDb,
    alembic_config,
    asyncpg_url,
    create_blastproof_scratch,
    drop_blastproof_scratch,
    leftover_shadow_databases,
    on_database,
    replit_impact,
)
from tests.impact_helpers import FakeLlm, report
from tests.judge_helpers import FakeJudgeLlm, verdict
from tests.knowledge_helpers import WordEmbedder
from tests.workflow_helpers import CALM_HEADLINE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

INCIDENTS = SCENARIOS_DIR / "incidents.yaml"
LABELED = SCENARIOS_DIR / "labeled.yaml"
FILES = [INCIDENTS, LABELED]
PLANTED_EMAIL = "jane.doe@example.org"

SUBSET = frozenset(
    {
        "replit-clean-up-inactive-users",  # previewed, held for review
        "drop-users-table",  # blocked in pass 1, no preview
        "note-on-customer-42",  # fast lane
        "open-support-ticket",  # previewed, allowed
        "psql-direct-delete",  # a hook scenario
    }
)


class RoutingModel:
    """What `model_factory` returns: the summary runnable or the judge runnable by schema."""

    def __init__(self, summary: FakeLlm, judge: FakeJudgeLlm) -> None:
        self.summary = summary
        self.judge = judge

    def with_structured_output(self, schema: Any, *, include_raw: bool = False) -> FakeLlm:
        return self.judge if schema.__name__ == "JudgeVerdict" else self.summary


def models(summary: FakeLlm | None = None, judge: FakeJudgeLlm | None = None) -> Any:
    model = RoutingModel(
        summary or FakeLlm(parsed=report(CALM_HEADLINE)),
        judge or FakeJudgeLlm(parsed=verdict("allow", "low", 0.9, "Looks routine for this table.")),
    )
    return lambda name, options, settings: model


def live_settings(db: AuthDb, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "company_connection_profile": "local",
        "neon_client": "fake",
        "blastproof_database_url": asyncpg_url(db.app_url),
        "checkpointer_database_url": db.app_url,
        "local_company_reader_url": COMPANY_READER_URL,
        "local_company_writer_url": COMPANY_WRITER_URL,
        "fake_neon_admin_url": COMPANY_ADMIN_URL,
    } | overrides
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def uncached_loggers() -> None:
    # `capture_logs` misses a logger that cached its processors earlier (see
    # test_workflow_fail_closed.py).
    graph_module.logger.__dict__.pop("bind", None)
    live_mode.logger.__dict__.pop("bind", None)


@pytest.fixture(scope="module")
def live_db() -> Iterator[AuthDb]:
    """A migrated scratch Blastproof database with a small knowledge base."""
    name = create_blastproof_scratch()
    try:
        alembic_command.upgrade(alembic_config(on_database(BLASTPROOF_OWNER_URL, name)), "head")
        db = AuthDb(name)
        run_sync(ingest_knowledge(db))
        yield db
    finally:
        drop_blastproof_scratch(name)


STORES: dict[str, MemoryVectorStore] = {}


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg's async mode (the checkpointer) cannot use Windows' Proactor loop.
    return asyncio.run(
        coro, loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    )


async def ingest_knowledge(db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(db.app_url))
    try:
        store = MemoryVectorStore(WordEmbedder.dimensions)
        service = KnowledgeService(make_sessionmaker(engine), WordEmbedder(), store)
        await service.ingest(
            "business_rules.md",
            b"# Users\n\nInactive users are kept for two years. Paying customers are kept.\n",
            ["users"],
        )
        STORES[db.name] = store
    finally:
        await engine.dispose()


def run_args(db: AuthDb) -> dict[str, Any]:
    return {"embedder": WordEmbedder(), "store": STORES[db.name]}


async def count_users() -> int:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        return await conn.fetchval("SELECT count(*) FROM users")
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def subset(live_db: AuthDb) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        users_before = await count_users()
        outcomes = await run_live(
            live_settings(live_db),
            FILES,
            model_factory=models(),
            only=SUBSET,
            timeout_seconds=120,
            **run_args(live_db),
        )
        return {
            "outcomes": {o.id: o for o in outcomes},
            "users_before": users_before,
            "users_after": await count_users(),
        }

    return run_sync(go())


def by_id(subset: dict[str, Any], scenario_id: str) -> Outcome:
    return subset["outcomes"][scenario_id]


async def test_each_path_gets_its_expected_decision_and_a_work_time(subset: dict[str, Any]) -> None:
    expected = {
        "replit-clean-up-inactive-users": ("review", "previewed"),
        "drop-users-table": ("block", "blocked"),
        "note-on-customer-42": ("allow", "fast_lane"),
        "open-support-ticket": ("allow", "previewed"),
    }
    for scenario_id, (decision, path) in expected.items():
        outcome = by_id(subset, scenario_id)
        assert (outcome.actual, outcome.path) == (decision, path), scenario_id
        assert outcome.error is None
        assert outcome.reason.strip()
        assert outcome.ms is not None and outcome.ms >= 0, scenario_id
    # A preview takes much longer than a decision made from the SQL alone.
    assert by_id(subset, "replit-clean-up-inactive-users").ms > by_id(subset, "drop-users-table").ms


async def test_hook_scenarios_run_in_live_mode_too(subset: dict[str, Any]) -> None:
    hook = by_id(subset, "psql-direct-delete")
    assert (hook.kind, hook.actual, hook.path) == ("hook", "block", "hook")


async def test_the_measured_facts_equal_the_replit_labels(subset: dict[str, Any]) -> None:
    replit = by_id(subset, "replit-clean-up-inactive-users")
    assert replit.facts_match is True
    assert replit_impact().table_changes["users"].deleted == 48213
    assert by_id(subset, "open-support-ticket").facts_match is True
    assert by_id(subset, "drop-users-table").facts_match is None  # never previewed


async def test_the_summary_came_from_the_model_and_the_judge_ran(subset: dict[str, Any]) -> None:
    replit = by_id(subset, "replit-clean-up-inactive-users")
    assert replit.summary_source == "llm"
    assert replit.ai_unavailable is False
    assert replit.incident
    metrics = summarize(list(subset["outcomes"].values()))
    assert metrics.summaries_called >= 2
    assert metrics.summary_pass_rate == 1.0
    assert metrics.incident_misses == ()
    assert metrics.errors == ()


async def test_company_main_is_unchanged(subset: dict[str, Any]) -> None:
    assert subset["users_after"] == subset["users_before"]
    assert await leftover_shadow_databases() == []


async def test_a_raising_judge_gives_review_with_ai_unavailable_and_stays_caught(
    live_db: AuthDb,
) -> None:
    judge = FakeJudgeLlm(raises=RuntimeError("model down"))
    outcomes = await run_live(
        live_settings(live_db),
        [LABELED],
        model_factory=models(judge=judge),
        only=frozenset({"delete-cancelled-orders"}),
        **run_args(live_db),
    )
    (outcome,) = outcomes
    assert outcome.actual == "review"
    assert outcome.ai_unavailable is True
    assert outcome.caught
    assert summarize(outcomes).ai_unavailable == 1


async def judge_prompts(db: AuthDb, variant: str) -> tuple[Outcome, str]:
    judge = FakeJudgeLlm(parsed=verdict("review", "high", 0.9, "Looks risky for this table."))
    (outcome,) = await run_live(
        live_settings(db),
        [INCIDENTS],
        model_factory=models(judge=judge),
        only=frozenset({"replit-clean-up-inactive-users"}),
        judge_variant=variant,  # type: ignore[arg-type]
        **run_args(db),
    )
    return outcome, repr(judge.calls)


async def test_the_no_rag_variant_still_runs_the_judge_but_without_the_business_rules(
    live_db: AuthDb,
) -> None:
    with_rag, rag_prompt = await judge_prompts(live_db, "rag")
    without, bare_prompt = await judge_prompts(live_db, "no-rag")
    assert "Paying customers are kept" in rag_prompt
    assert "Paying customers are kept" not in bare_prompt
    for outcome in (with_rag, without):
        assert outcome.error is None
        assert outcome.ai_unavailable is False
        assert outcome.actual == "review"


async def test_a_model_that_hangs_past_the_timeout_is_an_error_and_the_run_fails(
    live_db: AuthDb,
) -> None:
    outcomes = await run_live(
        live_settings(live_db, llm_timeout_seconds=60),
        [LABELED],
        model_factory=models(summary=FakeLlm(hang=True)),
        only=frozenset({"open-support-ticket"}),
        timeout_seconds=15,
        **run_args(live_db),
    )
    (outcome,) = outcomes
    assert outcome.error == "timeout"
    assert outcome.actual is None
    assert gate_failures(summarize(outcomes), outcomes) == [
        "scenario could not be measured: open-support-ticket (timeout)"
    ]
    assert await leftover_shadow_databases() == []


def test_run_main_exits_1_when_a_live_scenario_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, live_db: AuthDb
) -> None:
    async def fake_run_live(*args: Any, **kwargs: Any) -> list[Outcome]:
        return [Outcome("x", "labeled.yaml", "sql", "review", None, error="timeout")]

    async def no_reads(*args: Any, **kwargs: Any) -> list[Outcome]:
        return []

    monkeypatch.setattr(run, "Settings", lambda: live_settings(live_db))
    monkeypatch.setattr(live_mode, "run_live", fake_run_live)
    monkeypatch.setattr(live_mode, "measure_reads", no_reads)
    assert run.main(["--mode", "live", "--out", str(tmp_path / "live.json")]) == 1


async def test_the_read_overhead_has_rows_for_every_read_query(live_db: AuthDb) -> None:
    outcomes = await measure_reads(live_settings(live_db), repeat=2)
    assert all(o.error is None for o in outcomes), [o.error for o in outcomes]
    for name in READ_QUERIES:
        rows = [o for o in outcomes if o.id.startswith(f"read-{name}-")]
        assert len(rows) == 2, name
        assert all(o.path == "read_overhead" and o.ms is not None and o.ms >= 0 for o in rows)
    metrics = summarize(outcomes)
    assert metrics.latency["read_overhead"].count == 2 * len(READ_QUERIES)
    assert metrics.latency["read_overhead"].p95 is not None


def scenario_file(tmp_path: Path, anchor: str, intent: str) -> Path:
    path = tmp_path / "planted.yaml"
    path.write_text(
        f"anchor_date: {anchor}\n"
        "scenarios:\n"
        "  - kind: sql\n"
        "    id: planted-email\n"
        f"    intent: {intent}\n"
        "    sql: INSERT INTO support_tickets (user_id, subject, status, priority)"
        " VALUES (42, 'Cannot export', 'open', 'normal')\n"
        "    expected_decision: allow\n"
        "    decidable_from_sql: false\n",
        encoding="utf-8",
    )
    return path


async def test_a_planted_email_reaches_no_log_line_and_no_json(
    live_db: AuthDb, tmp_path: Path
) -> None:
    path = scenario_file(tmp_path, "2026-10-01", f"Open a ticket for {PLANTED_EMAIL}")
    with capture_logs() as logs:
        outcomes = await run_live(
            live_settings(live_db), [path], model_factory=models(), **run_args(live_db)
        )
    assert outcomes[0].error is None
    assert PLANTED_EMAIL not in repr(logs)
    out = tmp_path / "live.json"
    write_json(summarize(outcomes), outcomes, out)
    assert PLANTED_EMAIL not in out.read_text(encoding="utf-8")
    assert PLANTED_EMAIL not in repr(outcomes)


async def test_an_anchor_mismatch_is_refused(live_db: AuthDb, tmp_path: Path) -> None:
    path = scenario_file(tmp_path, "2026-09-01", "Open a ticket")
    with pytest.raises(LiveModeError, match="anchor date"):
        await run_live(live_settings(live_db), [path], model_factory=models(), **run_args(live_db))


async def test_no_knowledge_chunks_is_refused(tmp_path: Path) -> None:
    name = create_blastproof_scratch()
    try:
        alembic_command.upgrade(alembic_config(on_database(BLASTPROOF_OWNER_URL, name)), "head")
        with pytest.raises(LiveModeError, match="knowledge-scan"):
            await run_live(
                live_settings(AuthDb(name)), [INCIDENTS], model_factory=models(), only=frozenset()
            )
    finally:
        drop_blastproof_scratch(name)


@pytest.fixture
async def accounts_db() -> AsyncIterator[AuthDb]:
    name = create_blastproof_scratch()
    try:
        alembic_command.upgrade(alembic_config(on_database(BLASTPROOF_OWNER_URL, name)), "head")
        yield AuthDb(name)
    finally:
        drop_blastproof_scratch(name)


async def test_an_existing_eval_account_that_is_an_admin_is_refused(accounts_db: AuthDb) -> None:
    accounts_db.make_user(role="admin", email=EVAL_EMAIL)
    engine = make_engine(asyncpg_url(accounts_db.app_url))
    try:
        with pytest.raises(LiveModeError, match="not an active member"):
            await ensure_eval_accounts(make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def test_reads_on_a_database_whose_main_system_is_not_local_are_one_clear_error(
    accounts_db: AuthDb,
) -> None:
    engine = make_engine(asyncpg_url(accounts_db.app_url))
    try:
        async with make_sessionmaker(engine).begin() as session:
            await systems_db.get_or_create_system(
                session,
                name=MAIN_SYSTEM_NAME,
                connection_profile="neon",
                neon_project_id=None,
                neon_parent_branch_id=None,
            )
    finally:
        await engine.dispose()
    outcomes = await measure_reads(live_settings(accounts_db), repeat=1)
    (outcome,) = outcomes
    assert outcome.error == live_mode.PROFILE_MISMATCH_TEXT
    assert gate_failures(summarize(outcomes), outcomes) == [
        f"read query failed: reads: {live_mode.PROFILE_MISMATCH_TEXT}"
    ]


async def test_the_eval_accounts_are_created_once_and_reused(accounts_db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(accounts_db.app_url))
    try:
        sessionmaker = make_sessionmaker(engine)
        first = await ensure_eval_accounts(sessionmaker)
        second = await ensure_eval_accounts(sessionmaker)
        assert (first.user.id, first.agent.id, first.system.id) == (
            second.user.id,
            second.agent.id,
            second.system.id,
        )
        assert first.user.role == "member"
        assert first.system.connection_profile == "local"
    finally:
        await engine.dispose()
    [(count,)] = accounts_db.execute("SELECT count(*) FROM users WHERE email = %s", (EVAL_EMAIL,))
    assert count == 1


async def test_the_json_of_a_live_run_holds_no_sql_or_url(
    subset: dict[str, Any], tmp_path: Path
) -> None:
    outcomes = list(subset["outcomes"].values())
    out = tmp_path / "live.json"
    write_json(summarize(outcomes), outcomes, out, meta={"mode": "live"})
    text = out.read_text(encoding="utf-8")
    assert "postgresql" not in text
    assert "DELETE FROM users" not in text
    assert json.loads(text)["metrics"]["catch_rate"] == 1.0
