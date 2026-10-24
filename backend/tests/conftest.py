import asyncio
import asyncio.proactor_events
import asyncio.selector_events
import ipaddress
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asyncpg
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings
from app.main import create_app
from evals.schema import ExpectedImpact, SqlScenario, load_scenario_file

BLASTPROOF_OWNER_URL = os.environ.get(
    "TEST_BLASTPROOF_OWNER_URL",
    "postgresql://blastproof_owner:blastproof_owner@localhost:5432/blastproof",
)
BLASTPROOF_APP_URL = os.environ.get(
    "TEST_BLASTPROOF_APP_URL",
    "postgresql://blastproof_app:blastproof_app@localhost:5432/blastproof",
)


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
