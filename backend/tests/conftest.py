import asyncio
import asyncio.proactor_events
import asyncio.selector_events
import ipaddress
import json
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import asyncpg
import psycopg
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.core.auth import CurrentAgent, CurrentUser, get_current_agent, require_admin
from app.core.clock import FrozenClock
from app.core.security import generate_agent_key, hash_password
from app.main import create_app
from app.services.neon import FakeNeonClient
from app.services.shadow import ShadowRunner
from evals.schema import ExpectedImpact, SqlScenario, load_scenario_file

BLASTPROOF_OWNER_URL = os.environ.get(
    "TEST_BLASTPROOF_OWNER_URL",
    "postgresql://blastproof_owner:blastproof_owner@localhost:5432/blastproof",
)
BLASTPROOF_APP_URL = os.environ.get(
    "TEST_BLASTPROOF_APP_URL",
    "postgresql://blastproof_app:blastproof_app@localhost:5432/blastproof",
)
# The Compose superuser on blastproof-db. Test-only: it creates and drops scratch databases so
# migration tests never touch the dev "blastproof" database.
BLASTPROOF_ADMIN_URL = os.environ.get(
    "TEST_BLASTPROOF_ADMIN_URL", "postgresql://blastproof:blastproof@localhost:5432/blastproof"
)
BACKEND_DIR = Path(__file__).resolve().parent.parent


# The Compose company-db (step 3). The superuser is test/dev only, like FAKE_NEON_ADMIN_URL.
COMPANY_ADMIN_URL = os.environ.get(
    "TEST_COMPANY_ADMIN_URL", "postgresql://company:company@localhost:5433/postgres"
)
COMPANY_READER_URL = os.environ.get(
    "TEST_COMPANY_READER_URL",
    "postgresql://blastproof_reader:blastproof_reader@localhost:5433/company_main",
)
COMPANY_WRITER_URL = os.environ.get(
    "TEST_COMPANY_WRITER_URL",
    "postgresql://blastproof_writer:blastproof_writer@localhost:5433/company_main",
)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_COMPANY_DIR = REPO_ROOT / "demo_company"
SCENARIOS_DIR = REPO_ROOT / "backend" / "evals" / "scenarios"
REPLIT_SCENARIO_ID = "replit-clean-up-inactive-users"


def scenario_anchor_date() -> date:
    """The seed's anchor date, as the scenario files hold it (D-052: tests read it from there)."""
    return load_scenario_file(SCENARIOS_DIR / "incidents.yaml").anchor_date


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A clock fixed at midnight UTC on the anchor date, where the expected counts hold (D-079)."""
    anchor = scenario_anchor_date()
    return FrozenClock(datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC))


def replit_impact() -> ExpectedImpact:
    """The demo's headline facts (48,213 users, 12 paying), as incidents.yaml states them."""
    for scenario in load_scenario_file(SCENARIOS_DIR / "incidents.yaml").scenarios:
        if isinstance(scenario, SqlScenario) and scenario.id == REPLIT_SCENARIO_ID:
            assert scenario.expected_impact is not None
            return scenario.expected_impact
    raise AssertionError(f"{REPLIT_SCENARIO_ID} is missing from incidents.yaml")


def company_url(database: str) -> str:
    """The company-db superuser URL for another database on the same server."""
    return COMPANY_ADMIN_URL.rsplit("/", 1)[0] + f"/{database}"


def on_database(url: str, database: str) -> str:
    """The same login, connected to another database on the same server."""
    return url.rsplit("/", 1)[0] + f"/{database}"


def create_blastproof_scratch(*, with_vector: bool = True) -> str:
    """A fresh database on blastproof-db, set up like "blastproof" is by the init scripts.

    Repeats the per-database part of docker/blastproof-init/01-roles.sql (the roles themselves are
    server-wide and already exist) and 02-extensions.sql (unless with_vector is False).
    """
    name = f"bp_scratch_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(BLASTPROOF_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name} OWNER blastproof_owner")
        admin.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        admin.execute(f"GRANT CONNECT ON DATABASE {name} TO blastproof_app")
    with psycopg.connect(on_database(BLASTPROOF_ADMIN_URL, name), autocommit=True) as admin:
        admin.execute("ALTER SCHEMA public OWNER TO blastproof_owner")
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        admin.execute("GRANT USAGE ON SCHEMA public TO blastproof_app")
        if with_vector:
            admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return name


def drop_blastproof_scratch(name: str) -> None:
    with psycopg.connect(BLASTPROOF_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def alembic_config(url: str) -> AlembicConfig:
    """Alembic's config for backend/alembic.ini, pointed at `url` instead of the settings."""
    config = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["url"] = url
    return config


@pytest.fixture(scope="module")
def migrated_db(blastproof_db_reachable: None) -> Iterator[str]:
    """A scratch database migrated to head as blastproof_owner; dropped after the module."""
    name = create_blastproof_scratch()
    try:
        alembic_command.upgrade(alembic_config(on_database(BLASTPROOF_OWNER_URL, name)), "head")
        yield name
    finally:
        drop_blastproof_scratch(name)


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests never read backend/.env.
    return Settings(_env_file=None, app_env="test")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The context manager runs the app's lifespan (startup and shutdown).
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def blastproof_db_reachable() -> None:
    """Fail integration tests at once, with a clear message, when Postgres is not running.

    Fails rather than skips, so a missing database can never look like a passing run.
    """

    async def probe() -> None:
        conn = await asyncpg.connect(BLASTPROOF_OWNER_URL, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        pytest.fail(
            "blastproof-db is not reachable on localhost:5432. Run `docker compose up -d` from"
            " the repo root, or run only unit tests with `-m 'not integration'`."
            f" ({type(exc).__name__})",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def company_db_seeded() -> None:
    """Fail at once, with a clear message, when company-db is down or company_main is not seeded."""

    async def probe() -> None:
        conn = await asyncpg.connect(company_url("company_main"), timeout=3)
        try:
            await conn.fetchval("SELECT anchor_date FROM blastproof.seed_info")
        finally:
            await conn.close()

    try:
        asyncio.run(probe())
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        pytest.fail(
            "company_main is not ready on localhost:5433. Run `docker compose up -d`, then"
            " `uv run --project backend python demo_company/seed.py --target local` from the repo"
            f" root. ({type(exc).__name__})",
            pytrace=False,
        )


def run_demo_script(
    script: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a demo_company script as a subprocess (the backend never imports it, D-047).

    The child never reads backend/.env (SEED_ENV_FILE is empty) and sees only the URLs given here.
    """
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        not in {
            "SEED_NEON_OWNER_URL",
            "COMPANY_DB_READER_URL",
            "COMPANY_DB_WRITER_URL",
            "FAKE_NEON_ADMIN_URL",
        }
    }
    child_env |= {"SEED_ENV_FILE": "", "FAKE_NEON_ADMIN_URL": COMPANY_ADMIN_URL}
    child_env |= env or {}
    return subprocess.run(
        [sys.executable, str(DEMO_COMPANY_DIR / script), *args],
        env=child_env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )


class NetworkBlockedError(RuntimeError):
    pass


@dataclass
class NetworkGuard:
    blocked: list[str] = field(default_factory=list)

    def check(self, host: object) -> None:
        if not _is_loopback(host):
            self.blocked.append(str(host))
            raise NetworkBlockedError(f"network access blocked in tests: {host!r}")


def _is_loopback(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode()
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _psycopg_hosts(conninfo: str, kwargs: dict[str, object]) -> list[str | None]:
    params = conninfo_to_dict(conninfo, **{k: v for k, v in kwargs.items() if v is not None})
    # libpq falls back to PGHOST, then to a local socket / localhost.
    raw = params.get("hostaddr") or params.get("host") or os.environ.get("PGHOST")
    if not raw:
        return [None]
    return [h.strip() or None for h in str(raw).split(",")]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    """Block connections to anything but loopback (asyncio on Windows needs loopback).

    Covers every way our stack reaches the network from Python:
    - DNS lookups (`socket.getaddrinfo`), which every connection to a host name goes through;
    - `socket.connect` / `connect_ex` (requests, sync httpx, model downloads);
    - the event loop's `sock_connect`, used by asyncio for numeric IPs (asyncpg, async httpx).
      On Windows the proactor loop connects in C without calling `socket.connect`;
    - psycopg, whose libpq driver opens sockets in C and bypasses all of the above.
    Another C extension that opens its own sockets would not be caught; none is used at startup.

    Also fails the test afterwards if any attempt was made, in case the code swallowed the error.
    """
    guard = NetworkGuard()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        guard.check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self: socket.socket, address) -> None:  # type: ignore[no-untyped-def]
        if isinstance(address, tuple):
            guard.check(address[0])
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address) -> int:  # type: ignore[no-untyped-def]
        if isinstance(address, tuple):
            guard.check(address[0])
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)

    for loop_class in (
        asyncio.proactor_events.BaseProactorEventLoop,
        asyncio.selector_events.BaseSelectorEventLoop,
    ):
        real_sock_connect = loop_class.sock_connect

        async def guarded_sock_connect(self, sock, address, _real=real_sock_connect):  # type: ignore[no-untyped-def]
            if isinstance(address, tuple):
                guard.check(address[0])
            return await _real(self, sock, address)

        monkeypatch.setattr(loop_class, "sock_connect", guarded_sock_connect)

    def check_psycopg(conninfo: str, kwargs: dict[str, object]) -> None:
        for host in _psycopg_hosts(conninfo, kwargs):
            guard.check(host)

    real_sync_connect = psycopg.Connection.connect.__func__  # type: ignore[attr-defined]
    real_async_connect = psycopg.AsyncConnection.connect.__func__  # type: ignore[attr-defined]

    def guarded_sync_connect(cls, conninfo="", **kwargs):  # type: ignore[no-untyped-def]
        check_psycopg(conninfo, kwargs)
        return real_sync_connect(cls, conninfo, **kwargs)

    async def guarded_async_connect(cls, conninfo="", **kwargs):  # type: ignore[no-untyped-def]
        check_psycopg(conninfo, kwargs)
        return await real_async_connect(cls, conninfo, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(guarded_sync_connect))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(guarded_async_connect))
    # psycopg.connect is a copy of Connection.connect made at import time.
    monkeypatch.setattr(psycopg, "connect", psycopg.Connection.connect)

    yield guard
    blocked, guard.blocked = guard.blocked, []
    assert not blocked, f"code tried to reach the network: {blocked}"


# --- Step 5: auth fixtures ------------------------------------------------------------------

TEST_JWT_SECRET = "test-only-jwt-secret-" + "x" * 32
TEST_PASSWORD = "correct horse battery staple"
# Requests may set this header to choose the client IP the app sees (test apps only).
TEST_CLIENT_IP_HEADER = b"x-test-client-ip"


def asyncpg_url(url: str) -> str:
    """The same login as a SQLAlchemy asyncpg URL (what BLASTPROOF_DATABASE_URL holds)."""
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def with_client_ip(app: ASGIApp) -> ASGIApp:
    """Wrap an app so a request can pick its client IP with the X-Test-Client-IP header.

    One TestClient (one event loop, one connection pool) can then act as many IPs.
    """

    async def wrapper(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            ip = dict(scope["headers"]).get(TEST_CLIENT_IP_HEADER)
            if ip is not None:
                scope = {**scope, "client": (ip.decode(), 50000)}
        await app(scope, receive, send)

    return wrapper


@dataclass(frozen=True)
class TestUser:
    id: uuid.UUID
    email: str
    password: str


@dataclass(frozen=True)
class TestAgent:
    id: uuid.UUID
    key: str = field(repr=False)


@dataclass(frozen=True)
class AuthDb:
    """A migrated scratch database, with helpers that act as blastproof_app (like the app)."""

    name: str

    @property
    def app_url(self) -> str:
        return on_database(BLASTPROOF_APP_URL, self.name)

    def execute(self, sql: str, params: tuple = ()) -> list[tuple]:
        with psycopg.connect(self.app_url, autocommit=True) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def make_user(
        self,
        *,
        role: str = "member",
        is_active: bool = True,
        password: str = TEST_PASSWORD,
        email: str | None = None,
    ) -> TestUser:
        email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
        [(user_id,)] = self.execute(
            "INSERT INTO users (email, display_name, password_hash, role, is_active)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (email, f"User {email[:10]}", hash_password(password), role, is_active),
        )
        return TestUser(id=user_id, email=email, password=password)

    def make_agent(self, owner_id: uuid.UUID, *, is_active: bool = True) -> TestAgent:
        new = generate_agent_key()
        [(agent_id,)] = self.execute(
            "INSERT INTO agents (owner_id, name, api_key_hash, key_prefix, is_active)"
            " VALUES (%s, 'test agent', %s, %s, %s) RETURNING id",
            (owner_id, new.key_hash, new.key_prefix, is_active),
        )
        return TestAgent(id=agent_id, key=new.key)

    def failures(self, email: str) -> int:
        [(count,)] = self.execute(
            "SELECT count(*) FROM login_attempts WHERE email = %s AND NOT succeeded", (email,)
        )
        return count

    def backdate_attempts(self, email: str, by: timedelta) -> None:
        self.execute(
            "UPDATE login_attempts SET created_at = created_at - %s WHERE email = %s", (by, email)
        )


@pytest.fixture(scope="module")
def auth_db(migrated_db: str) -> AuthDb:
    return AuthDb(migrated_db)


@pytest.fixture
def auth_settings(auth_db: AuthDb) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=asyncpg_url(auth_db.app_url),
        jwt_secret=TEST_JWT_SECRET,
    )


def build_auth_test_app(settings: Settings) -> FastAPI:
    """The real app plus two test-only routes guarded by require_admin and get_current_agent."""
    app = create_app(settings)

    @app.get("/test/admin-only")
    async def admin_only(user: Annotated[CurrentUser, Depends(require_admin)]) -> dict[str, str]:
        return {"user_id": str(user.id)}

    @app.get("/test/agent")
    async def agent_only(
        agent: Annotated[CurrentAgent, Depends(get_current_agent)],
    ) -> dict[str, str]:
        return {"agent_id": str(agent.agent_id), "owner_id": str(agent.owner_id)}

    return app


@pytest.fixture
def auth_client(auth_settings: Settings) -> Iterator[TestClient]:
    with TestClient(with_client_ip(build_auth_test_app(auth_settings))) as test_client:
        yield test_client


def login(
    client: TestClient, email: str, password: str, ip: str = "203.0.113.1"
):  # -> the TestClient's response (httpx2, not a direct dependency)
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        headers={TEST_CLIENT_IP_HEADER.decode(): ip},
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Step 6: MCP helpers ----------------------------------------------------------------------

# The MCP DNS-rebinding check refuses TestClient's default host `testserver` with 421 (D-099).
MCP_BASE_URL = "http://localhost:8000"
MCP_PROTOCOL_VERSION = "2026-07-28"


def mcp_settings(auth_db: AuthDb, **overrides: object) -> Settings:
    """auth_settings plus the local company database (company_main as blastproof_reader)."""
    values: dict[str, object] = {
        "blastproof_database_url": asyncpg_url(auth_db.app_url),
        "jwt_secret": TEST_JWT_SECRET,
        "company_connection_profile": "local",
        "local_company_reader_url": COMPANY_READER_URL,
    }
    return Settings(_env_file=None, app_env="test", **(values | overrides))  # type: ignore[arg-type]


@pytest.fixture
def mcp_client(auth_db: AuthDb) -> Iterator[TestClient]:
    with TestClient(create_app(mcp_settings(auth_db)), base_url=MCP_BASE_URL) as test_client:
        yield test_client


def mcp_call(tool: str, arguments: dict[str, object] | None = None) -> tuple[dict, dict[str, str]]:
    """A raw 2026-07-28 `tools/call` (the session-less protocol Claude Code speaks, D-097).

    Returns the JSON-RPC body and the headers to send with it (without the agent key).
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": arguments or {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "blastproof-tests", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": MCP_PROTOCOL_VERSION,
        "mcp-method": "tools/call",
        "mcp-name": tool,
    }
    return body, headers


def parse_rpc(content_type: str, text: str) -> dict:
    """The JSON-RPC response from an SSE stream (`data:` lines) or a plain JSON body."""
    if content_type.startswith("text/event-stream"):
        for line in text.splitlines():
            if line.startswith("data:") and line[5:].strip():
                message = json.loads(line[5:])
                if "id" in message:
                    return message
        raise AssertionError(f"no JSON-RPC response in the SSE stream: {text[:200]!r}")
    return json.loads(text)


def call_tool(
    client: TestClient, key: str, tool: str, arguments: dict[str, object] | None = None
) -> dict:
    """Call a tool with an agent key and return its structured result (asserts HTTP 200)."""
    body, headers = mcp_call(tool, arguments)
    response = client.post("/mcp", json=body, headers=headers | bearer(key))
    assert response.status_code == 200, response.text[:300]
    message = parse_rpc(response.headers.get("content-type", ""), response.text)
    result = message["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


# --- Step 10: shadow runs ---------------------------------------------------------------------


def fake_neon_client(clock: FrozenClock | None = None, **overrides: object) -> FakeNeonClient:
    """A FakeNeonClient on the Compose company-db: clones company_main, never calls Neon."""
    from app.core.clock import SystemClock

    values: dict[str, object] = {
        "admin_url": COMPANY_ADMIN_URL,
        "writer_url": COMPANY_WRITER_URL,
        "clock": clock or SystemClock(),
    }
    return FakeNeonClient(**(values | overrides))  # type: ignore[arg-type]


def shadow_runner(clients: object | None = None, **overrides: object) -> ShadowRunner:
    from app.config import DEFAULT_POLICY_FILE
    from app.services.policy import load_policy

    values: dict[str, object] = {
        "policy": load_policy(DEFAULT_POLICY_FILE),
        "clients": clients if clients is not None else {"local": fake_neon_client()},
        # The real default is 30 s; a preview of the 273k-row seed takes 10 to 15 s on a Docker
        # Desktop machine, so tests that are not about the timeout leave room.
        "timeout_seconds": 120,
    }
    return ShadowRunner(**(values | overrides))  # type: ignore[arg-type]


async def leftover_shadow_databases() -> list[str]:
    conn = await asyncpg.connect(company_url("postgres"))
    try:
        rows = await conn.fetch(r"SELECT datname FROM pg_database WHERE datname LIKE 'shadow\_%'")
        return [r["datname"] for r in rows]
    finally:
        await conn.close()
