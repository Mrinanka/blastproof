"""`seed-demo-actions` (BE-121): real graph, real shadow runs on the Compose company-db, scripted
models, stub commit. One seed run per module (a full run is 1 to 2 minutes); the tests read it.

Tests that change the seeded actions (approve, reject) come last in the file, and the refusal tests
use scratch databases of their own.
"""

import asyncio
import dataclasses
import sys
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

import asyncpg
import pytest
import structlog

from app.config import Settings
from app.db.enums import ALLOWED_TRANSITIONS, ActionStatus
from app.services import auth as auth_service
from app.services import demo_seed
from app.services.demo_seed import (
    DEMO_SYSTEM_NAME,
    DEMO_USERS,
    SCENARIOS,
    SeedError,
    SeedReport,
    seed_demo_actions,
)
from tests.action_helpers import token_for
from tests.conftest import (
    BLASTPROOF_OWNER_URL,
    COMPANY_ADMIN_URL,
    COMPANY_READER_URL,
    COMPANY_WRITER_URL,
    TEST_JWT_SECRET,
    AuthDb,
    TestUser,
    alembic_command,
    alembic_config,
    asyncpg_url,
    create_blastproof_scratch,
    drop_blastproof_scratch,
    leftover_shadow_databases,
    on_database,
)
from tests.knowledge_helpers import WordEmbedder
from tests.test_decisions_api import api_for
from tests.workflow_helpers import (
    action_row,
    checkpoint_count,
    make_env,
    rig,
    runner_for,
    status_history,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

PASSWORD = "demo-password-for-tests"
ASKED: list[bool] = []  # one entry per password prompt
EXPECTED_STATUSES = {"awaiting_review", "rejected", "expired", "blocked", "committed", "failed"}
NO_MODEL_CALL = {"u1-committed-auto", "u1-blocked-drop", "u2-blocked-prices"} | {
    "u1-failed-unknown-table",
    "u2-failed-unknown-table",
}
OWNERS = tuple(email for email, _ in DEMO_USERS)


def settings_for(db: AuthDb, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "blastproof_database_url": asyncpg_url(db.app_url),
        "checkpointer_database_url": db.app_url,
        "local_company_writer_url": COMPANY_WRITER_URL,
        "fake_neon_admin_url": COMPANY_ADMIN_URL,
        "jwt_secret": TEST_JWT_SECRET,
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[arg-type]


def run_seed(db: AuthDb, *, password: str | None = PASSWORD, **options: object) -> SeedReport:
    """The seed on a Selector loop (psycopg async cannot use Windows' Proactor loop)."""
    overrides = options.pop("settings", {})

    def provider() -> str:
        ASKED.append(True)
        return password  # type: ignore[return-value]

    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(
        seed_demo_actions(
            settings_for(db, **overrides),  # type: ignore[arg-type]
            password=provider if password is not None else None,
            embedder=WordEmbedder(),
            **options,  # type: ignore[arg-type]
        ),
        loop_factory=factory,
    )


def scalar(db: AuthDb, sql: str, params: tuple = ()) -> object:
    [(value,)] = db.execute(sql, params)
    return value


async def company_counts() -> dict[str, int]:
    conn = await asyncpg.connect(COMPANY_READER_URL)
    try:
        return {
            table: await conn.fetchval(f"SELECT count(*) FROM {table}")
            for table in ("users", "orders", "support_tickets", "customer_notes", "products")
        }
    finally:
        await conn.close()


@dataclass
class Seeded:
    db: AuthDb
    report: SeedReport
    logs: list[dict]
    counts_before: dict[str, int]
    counts_after: dict[str, int]


@pytest.fixture(scope="module")
def seeded(blastproof_db_reachable: None, company_db_seeded: None) -> Iterator[Seeded]:
    name = create_blastproof_scratch()
    try:
        alembic_command.upgrade(alembic_config(on_database(BLASTPROOF_OWNER_URL, name)), "head")
        db = AuthDb(name)
        before = asyncio.run(company_counts())
        with structlog.testing.capture_logs() as logs:
            report = run_seed(db)
        yield Seeded(db, report, logs, before, asyncio.run(company_counts()))
    finally:
        drop_blastproof_scratch(name)


def actions_of(seeded: Seeded, email: str) -> list[tuple]:
    return seeded.db.execute(
        "SELECT a.id, a.status FROM actions a JOIN users u ON u.id = a.user_id WHERE u.email = %s",
        (email,),
    )


# --- the done-when -----------------------------------------------------------------------------


def test_both_users_have_actions_in_every_status(seeded: Seeded) -> None:
    assert seeded.report.ok, [a for a in seeded.report.actions if not a.ok]
    for email in OWNERS:
        assert {status for _, status in actions_of(seeded, email)} >= EXPECTED_STATUSES
    assert len(seeded.report.actions) == len(SCENARIOS)


def test_each_scenario_ended_in_its_expected_status(seeded: Seeded) -> None:
    for scenario, action in zip(SCENARIOS, seeded.report.actions, strict=True):
        assert (action.key, action.status) == (scenario.key, scenario.expect.value)
        assert action.owner_email == OWNERS[scenario.owner - 1]


def test_every_status_history_follows_the_state_machine(seeded: Seeded) -> None:
    env = make_env(seeded.db)  # only for its database handle
    for action in seeded.report.actions:
        history = [ActionStatus(s) for s in status_history(env, action.action_id)]
        assert history[0] == ActionStatus.RECEIVED
        for before, after in zip(history, history[1:], strict=False):
            assert after in ALLOWED_TRANSITIONS[before], (action.key, before, after)
        assert history[-1].value == action.status


async def test_pending_actions_are_paused_at_the_review_with_a_checkpoint(seeded: Seeded) -> None:
    env = make_env(seeded.db)
    pending = [a for a in seeded.report.actions if a.status == "awaiting_review"]
    assert len(pending) == 2
    async with rig(env) as r:
        for action in pending:
            assert await r.next_nodes(action.action_id) == ("human_review",)
            assert checkpoint_count(env, action.action_id) > 0
            assert action_row(env, action.action_id)["expires_at"] is not None


def test_the_expired_actions_expired_without_a_decider(seeded: Seeded) -> None:
    for action in seeded.report.actions:
        if action.status == "expired":
            row = action_row(make_env(seeded.db), action.action_id)
            assert row["decided_by"] is None and row["expires_at"] is not None


def test_llm_path_summaries_are_scripted_and_the_numbers_are_measured(seeded: Seeded) -> None:
    env = make_env(seeded.db)
    replit = next(a for a in seeded.report.actions if a.key == "u1-pending-replit")
    row = action_row(env, replit.action_id)
    assert row["impact_report"]["source"] == "llm"
    assert row["decision"] == "review" and row["judge"]["result"]["recommendation"] == "review"
    changed = sum(
        sum(t[k] for k in ("deleted", "updated", "inserted"))
        for t in row["impact"]["table_changes"].values()
    )
    assert changed == scalar(
        seeded.db, "SELECT count(*) FROM preview_rows WHERE action_id = %s", (replit.action_id,)
    )
    assert changed > 0
    assert row["llm_cost_usd"] > 0  # the fake usage is priced from LLM_PRICES
    for action in seeded.report.actions:
        # The fast lane and the pass-1 blocks never reach a model; a failed run never summarizes.
        if action.key in NO_MODEL_CALL:
            continue
        assert action_row(env, action.action_id)["impact_report"]["source"] == "llm", action.key


def test_blocked_and_failed_actions_say_why(seeded: Seeded) -> None:
    env = make_env(seeded.db)
    by_key = {a.key: a for a in seeded.report.actions}
    assert action_row(env, by_key["u1-blocked-drop"].action_id)["decision"] == "block"
    failed = action_row(env, by_key["u1-failed-unknown-table"].action_id)
    assert failed["status"] == "failed" and failed["failure_reason"]


def test_the_actions_use_the_demo_system_on_the_local_profile(seeded: Seeded) -> None:
    rows = seeded.db.execute(
        "SELECT DISTINCT s.name, s.connection_profile FROM actions a JOIN systems s"
        " ON s.id = a.system_id"
    )
    assert rows == [(DEMO_SYSTEM_NAME, "local")]


def test_demo_users_are_active_members_with_one_agent_each(seeded: Seeded) -> None:
    rows = seeded.db.execute(
        "SELECT u.email, u.role, u.is_active, count(a.id) FROM users u LEFT JOIN agents a"
        " ON a.owner_id = u.id WHERE u.email LIKE 'demo-user-%%'"
        " GROUP BY u.email, u.role, u.is_active ORDER BY u.email"
    )
    assert rows == [(email, "member", True, 1) for email in OWNERS]


async def test_the_company_database_is_untouched_and_no_shadow_is_left(seeded: Seeded) -> None:
    assert seeded.counts_after == seeded.counts_before
    assert await leftover_shadow_databases() == []


def test_no_password_or_key_reached_the_logs_or_the_report(seeded: Seeded) -> None:
    assert seeded.logs, "the seed logged nothing: the capture is not working"
    text = repr(seeded.logs) + repr(seeded.report)
    assert PASSWORD not in text and "bp_" not in text
    assert "DELETE FROM" not in repr(seeded.logs)


def test_the_password_was_asked_once_for_both_users(seeded: Seeded) -> None:
    assert ASKED == [True]


# --- re-running and --add-pending --------------------------------------------------------------


def test_a_second_run_reuses_the_accounts_and_never_asks_for_a_password(seeded: Seeded) -> None:
    before = Counter(
        scalar(seeded.db, f"SELECT count(*) FROM {table}") for table in ("users", "agents")
    )
    systems = scalar(seeded.db, "SELECT count(*) FROM systems")
    actions = scalar(seeded.db, "SELECT count(*) FROM actions")
    report = run_seed(seeded.db)  # the users exist: the provider must not be called
    assert report.ok
    assert ASKED == [True]  # still only the first run's single prompt
    assert (
        Counter(scalar(seeded.db, f"SELECT count(*) FROM {table}") for table in ("users", "agents"))
        == before
    )
    assert scalar(seeded.db, "SELECT count(*) FROM systems") == systems
    assert scalar(seeded.db, "SELECT count(*) FROM actions") == actions + len(SCENARIOS)


def test_add_pending_adds_one_review_action_for_user_one(seeded: Seeded) -> None:
    users = scalar(seeded.db, "SELECT count(*) FROM users")
    before = len(actions_of(seeded, OWNERS[0]))
    report = run_seed(seeded.db, password=None, add_pending=True)
    assert [(a.status, a.owner_email) for a in report.actions] == [("awaiting_review", OWNERS[0])]
    assert len(actions_of(seeded, OWNERS[0])) == before + 1
    assert scalar(seeded.db, "SELECT count(*) FROM users") == users


def test_a_planted_email_in_a_seeded_statement_never_reaches_the_logs(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "planted.secret@example.com"
    scenario = dataclasses.replace(
        demo_seed.ADD_PENDING,
        sql=f"UPDATE users SET plan = 'free' WHERE email = '{planted}'",
        intent=f"Downgrade {planted}",
    )
    monkeypatch.setattr(demo_seed, "ADD_PENDING", scenario)
    with structlog.testing.capture_logs() as logs:
        report = run_seed(seeded.db, password=None, add_pending=True)
    assert logs, "the seed logged nothing: the capture is not working"
    assert planted not in repr(logs) + repr(report)


# --- refusals (scratch databases of their own) --------------------------------------------------


def test_add_pending_before_the_seed_has_run_is_an_error(fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SeedError, match="not run yet"):
        run_seed(fresh_wf_env.db, password=None, add_pending=True)
    assert scalar(fresh_wf_env.db, "SELECT count(*) FROM actions") == 0


def test_production_is_refused(fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SeedError, match="production"):
        run_seed(fresh_wf_env.db, settings={"app_env": "production"})
    assert scalar(fresh_wf_env.db, "SELECT count(*) FROM actions") == 0


@pytest.mark.parametrize(
    "missing",
    [
        "blastproof_database_url",
        "checkpointer_database_url",
        "local_company_writer_url",
        "fake_neon_admin_url",
    ],
)
def test_a_missing_url_is_refused_by_name(missing: str, fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SeedError, match=missing.upper()):
        run_seed(fresh_wf_env.db, settings={missing: None})


def test_a_system_with_another_profile_is_refused(fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    db = fresh_wf_env.db
    db.execute(
        "INSERT INTO systems (name, connection_profile) VALUES (%s, 'neon')", (DEMO_SYSTEM_NAME,)
    )
    with pytest.raises(SeedError, match="another profile"):
        run_seed(db)
    assert scalar(db, "SELECT count(*) FROM actions") == 0


@pytest.mark.parametrize("kind", ["admin", "inactive"])
def test_a_demo_email_that_is_not_an_active_member_is_refused(kind: str, fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    db = fresh_wf_env.db
    db.make_user(
        email=OWNERS[0], role="admin" if kind == "admin" else "member", is_active=kind != "inactive"
    )
    with pytest.raises(SeedError, match="not an active member"):
        run_seed(db)
    assert scalar(db, "SELECT count(*) FROM actions") == 0


def test_a_short_password_is_refused_and_creates_nothing(fresh_wf_env) -> None:  # type: ignore[no-untyped-def]
    db = fresh_wf_env.db
    with pytest.raises(auth_service.AccountError):
        run_seed(db, password="short")
    assert scalar(db, "SELECT count(*) FROM actions") == 0


# --- approving and rejecting through the API (DW-B19, first half) --------------------------------
# These change the seeded actions, so they come last.


def demo_user(seeded: Seeded, email: str) -> TestUser:
    [(user_id,)] = seeded.db.execute("SELECT id FROM users WHERE email = %s", (email,))
    return TestUser(id=user_id, email=email, password=PASSWORD)


def pending_of(seeded: Seeded, key: str) -> uuid.UUID:
    return next(a.action_id for a in seeded.report.actions if a.key == key)


async def test_the_owner_approves_and_rejects_seeded_pending_actions(seeded: Seeded) -> None:
    env = make_env(seeded.db)
    one, two = (demo_user(seeded, email) for email in OWNERS)
    mine = pending_of(seeded, "u1-pending-replit")
    theirs = pending_of(seeded, "u2-pending-orders")
    async with rig(env) as r:
        runner = runner_for(r)
        async with api_for(env, runner) as api:
            # Another user's action is a 404, never a 403.
            assert (await api.approve(two, mine)).status_code == 404
            assert (
                await api.client.get(f"/v1/actions/{mine}", headers=token_for(two))
            ).status_code == 404

            approved = await api.approve(one, mine)
            assert approved.status_code == 200, approved.text
            await runner.join()
            assert action_row(env, mine)["status"] == "committed"  # the stub: nothing applied

            rejected = await api.reject(two, theirs, "Keep the cancelled orders for the audit.")
            assert rejected.status_code == 200, rejected.text
            await runner.join()
            row = action_row(env, theirs)
            assert row["status"] == "rejected"
            assert (await api.detail(two, theirs))["reviewer_note"] == (
                "Keep the cancelled orders for the audit."
            )
