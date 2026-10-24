import asyncio
import asyncio.proactor_events
import asyncio.selector_events
import ipaddress
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field

import asyncpg
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings
from app.main import create_app

BLASTPROOF_OWNER_URL = os.environ.get(
    "TEST_BLASTPROOF_OWNER_URL",
    "postgresql://blastproof_owner:blastproof_owner@localhost:5432/blastproof",
)
BLASTPROOF_APP_URL = os.environ.get(
    "TEST_BLASTPROOF_APP_URL",
    "postgresql://blastproof_app:blastproof_app@localhost:5432/blastproof",
)


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
