"""SEC-08, DW-B04: blastproof_app's privileges and the append-only audit log.

Runs against a scratch database migrated to head (conftest.migrated_db). Needs
`docker compose up -d`; fails rather than skips if Postgres is down.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.enums import ActionStatus, AuditEventType, ConnectionProfile
from app.db.models import Action, Agent, AuditEvent, System, User
from tests.conftest import BLASTPROOF_APP_URL, BLASTPROOF_OWNER_URL, on_database

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

AUDIT = "audit_events"
WORKING_TABLES = [
    "users", "login_attempts", "agents", "systems", "actions", "preview_rows", "before_images",
    "hook_events", "knowledge_chunks", "chunk_embeddings",
]  # fmt: skip
ALL_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]


@pytest.fixture
async def owner(migrated_db: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(on_database(BLASTPROOF_OWNER_URL, migrated_db))
    yield conn
    await conn.close()


@pytest.fixture
async def app(migrated_db: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(on_database(BLASTPROOF_APP_URL, migrated_db))
    yield conn
    await conn.close()


async def new_user(conn: asyncpg.Connection, email: str | None = None) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO users (email, display_name, password_hash) VALUES ($1, 'Test', 'x')"
        " RETURNING id",
        email or f"{uuid.uuid4().hex}@example.com",
    )


async def new_action(conn: asyncpg.Connection) -> uuid.UUID:
    """A user, an agent, a system and one action, for audit events to point at."""
    user_id = await new_user(conn)
    agent_id = await conn.fetchval(
        "INSERT INTO agents (owner_id, name, api_key_hash, key_prefix)"
        " VALUES ($1, 'agent', $2, 'bp_abcde') RETURNING id",
        user_id,
        uuid.uuid4().hex,
    )
    system_id = await conn.fetchval(
        "INSERT INTO systems (name, connection_profile) VALUES ($1, 'local') RETURNING id",
        f"system-{uuid.uuid4().hex}",
    )
    return await conn.fetchval(
        "INSERT INTO actions (user_id, agent_id, system_id, tool, status)"
        " VALUES ($1, $2, $3, 'query_database', 'received') RETURNING id",
        user_id,
        agent_id,
        system_id,
    )


async def new_audit_event(conn: asyncpg.Connection) -> uuid.UUID:
    action_id = await new_action(conn)
    await conn.execute(
        "INSERT INTO audit_events (action_id, event_type) VALUES ($1, 'received')", action_id
    )
    return action_id


# --- Done when (DW-B04): nobody can update or delete an audit event ------------------------------


async def test_app_can_append_and_read_audit_events(owner: asyncpg.Connection, app) -> None:
    action_id = await new_action(owner)
    await app.execute(
        "INSERT INTO audit_events (action_id, event_type, data) VALUES ($1, 'received', '{}')",
        action_id,
    )
    assert await app.fetchval("SELECT count(*) FROM audit_events WHERE action_id = $1", action_id)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET event_type = 'failed' WHERE action_id = $1",
        "DELETE FROM audit_events WHERE action_id = $1",
    ],
    ids=["update", "delete"],
)
async def test_app_cannot_change_audit_events(
    owner: asyncpg.Connection, app: asyncpg.Connection, statement: str
) -> None:
    action_id = await new_audit_event(owner)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
        await app.execute(statement, action_id)
    assert (
        await owner.fetchval("SELECT event_type FROM audit_events WHERE action_id = $1", action_id)
        == "received"
    )


async def test_app_cannot_truncate_audit_events(app: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
        await app.execute("TRUNCATE audit_events")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET event_type = 'failed' WHERE action_id = $1",
        "DELETE FROM audit_events WHERE action_id = $1",
    ],
    ids=["update", "delete"],
)
async def test_owner_cannot_change_audit_events(owner: asyncpg.Connection, statement: str) -> None:
    # The owner has every table privilege; only the trigger stops it (D-013).
    action_id = await new_audit_event(owner)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        await owner.execute(statement, action_id)
    assert (
        await owner.fetchval("SELECT event_type FROM audit_events WHERE action_id = $1", action_id)
        == "received"
    )


async def test_owner_cannot_truncate_audit_events(owner: asyncpg.Connection) -> None:
    # TRUNCATE skips row-level DELETE triggers, so the trigger is statement-level (D-083).
    await new_audit_event(owner)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only: TRUNCATE"):
        await owner.execute("TRUNCATE audit_events CASCADE")
    assert await owner.fetchval("SELECT count(*) FROM audit_events") > 0


async def test_the_trigger_fires_even_when_no_row_matches(owner: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only: UPDATE"):
        await owner.execute("UPDATE audit_events SET data = '{}' WHERE false")


# --- least privilege for the running app ---------------------------------------------------------


async def test_app_privileges_are_exactly_as_planned(owner: asyncpg.Connection) -> None:
    expected = {table: {"SELECT", "INSERT", "UPDATE", "DELETE"} for table in WORKING_TABLES}
    expected[AUDIT] = {"SELECT", "INSERT"}
    expected["alembic_version"] = set()
    actual = {}
    for table in expected:
        actual[table] = {
            privilege
            for privilege in ALL_PRIVILEGES
            if await owner.fetchval(
                "SELECT has_table_privilege('blastproof_app', $1, $2)", table, privilege
            )
        }
    assert actual == expected


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.app_made (id int)",
        "ALTER TABLE users ADD COLUMN extra int",
        "DROP TABLE hook_events",
        "ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only",
        "DROP TRIGGER audit_events_append_only ON audit_events",
    ],
    ids=["create", "alter", "drop", "disable-trigger", "drop-trigger"],
)
async def test_app_cannot_change_the_schema(app: asyncpg.Connection, statement: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app.execute(statement)


# --- constraints reject bad data, whoever writes it ----------------------------------------------


async def test_status_pending_is_never_stored(owner: asyncpg.Connection) -> None:
    action_id = await new_action(owner)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_actions_status"):
        await owner.execute("UPDATE actions SET status = 'pending' WHERE id = $1", action_id)


async def test_unknown_role_is_rejected(app: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="ck_users_role"):
        await app.execute(
            "INSERT INTO users (email, display_name, password_hash, role)"
            " VALUES ($1, 'Eve', 'x', 'superadmin')",
            f"{uuid.uuid4().hex}@example.com",
        )


async def test_email_is_unique_ignoring_case(app: asyncpg.Connection) -> None:
    local = uuid.uuid4().hex
    await new_user(app, f"{local}@Example.com")
    with pytest.raises(asyncpg.UniqueViolationError, match="uq_users_email_lower"):
        await new_user(app, f"{local.upper()}@example.COM")


async def test_api_key_hash_is_unique(app: asyncpg.Connection) -> None:
    user_id = await new_user(app)
    insert = (
        "INSERT INTO agents (owner_id, name, api_key_hash, key_prefix)"
        " VALUES ($1, $2, 'same-hash-' || $3, 'bp_abcde')"
    )
    marker = uuid.uuid4().hex
    await app.execute(insert, user_id, "one", marker)
    with pytest.raises(asyncpg.UniqueViolationError, match="uq_agents_api_key_hash"):
        await app.execute(insert, user_id, "two", marker)


async def test_audit_event_needs_a_real_action(app: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await app.execute(
            "INSERT INTO audit_events (action_id, event_type) VALUES ($1, 'received')",
            uuid.uuid4(),
        )


# --- the models work for the app role, through SQLAlchemy ----------------------------------------


async def test_orm_round_trip_as_the_app(migrated_db: str) -> None:
    url = on_database(BLASTPROOF_APP_URL, migrated_db).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = User(
                email=f"{uuid.uuid4().hex}@example.com", display_name="Ada", password_hash="x"
            )
            session.add(user)
            await session.flush()
            agent = Agent(
                owner_id=user.id, name="claude", api_key_hash=uuid.uuid4().hex, key_prefix="bp_abcd"
            )
            system = System(
                name=f"local-{uuid.uuid4().hex}", connection_profile=ConnectionProfile.LOCAL
            )
            session.add_all([agent, system])
            await session.flush()
            action = Action(
                user_id=user.id,
                agent_id=agent.id,
                system_id=system.id,
                tool="query_database",
                status=ActionStatus.RECEIVED,
                tables=["users"],
                pinned_values={"now()": "2026-10-01T00:00:00Z"},
            )
            session.add(action)
            await session.flush()
            session.add(AuditEvent(action_id=action.id, event_type=AuditEventType.RECEIVED))
            await session.commit()

            await session.refresh(action)
            assert isinstance(action.id, uuid.UUID)
            assert action.tables == ["users"]
            assert action.pinned_values == {"now()": "2026-10-01T00:00:00Z"}
            # CONV-06: timestamps come back time-zone aware, in UTC.
            assert action.created_at.utcoffset() == timedelta(0)
            assert user.role == "member" and user.token_version == 0 and user.is_active
    finally:
        await engine.dispose()
