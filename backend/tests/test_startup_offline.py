"""TEST-07: with APP_ENV=test, startup calls no outside service and downloads no model."""

import asyncio
import socket

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from tests.conftest import NetworkBlockedError, NetworkGuard

# Non-routable addresses: if the guard failed, these would hang until timeout, not connect.
OUTSIDE_IP = "10.255.255.1"


def expect_blocked(guard: NetworkGuard, *hosts: str) -> None:
    # These attempts were expected; clear them so the fixture's teardown check passes.
    assert guard.blocked == list(hosts)
    guard.blocked.clear()


def test_guard_blocks_dns_and_socket_connect(no_network: NetworkGuard) -> None:
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("example.com", 80), timeout=1)
    with pytest.raises(NetworkBlockedError), socket.socket() as sock:
        sock.connect((OUTSIDE_IP, 80))
    expect_blocked(no_network, "example.com", OUTSIDE_IP)


def test_guard_blocks_asyncio_numeric_ip(no_network: NetworkGuard) -> None:
    # asyncio skips DNS for numeric IPs, and on Windows connects without socket.connect.
    async def connect() -> None:
        await asyncio.wait_for(asyncio.open_connection(OUTSIDE_IP, 9), timeout=2)

    with pytest.raises(NetworkBlockedError):
        asyncio.run(connect())
    expect_blocked(no_network, OUTSIDE_IP)


def test_guard_blocks_psycopg(no_network: NetworkGuard) -> None:
    # libpq opens its sockets in C, so only the psycopg-level patch can stop it.
    with pytest.raises(NetworkBlockedError):
        psycopg.connect(f"postgresql://u:p@{OUTSIDE_IP}:5432/db", connect_timeout=2)
    with pytest.raises(NetworkBlockedError):
        psycopg.Connection.connect(host="db.example.com", connect_timeout=2)

    async def connect_async() -> None:
        await psycopg.AsyncConnection.connect(f"host={OUTSIDE_IP} connect_timeout=2")

    with pytest.raises(NetworkBlockedError):
        asyncio.run(connect_async())
    expect_blocked(no_network, OUTSIDE_IP, "db.example.com", OUTSIDE_IP)


def test_guard_allows_loopback(no_network: NetworkGuard) -> None:
    # Port 9 on loopback is closed: a refused connection proves the guard let it through.
    async def connect() -> None:
        await asyncio.open_connection("127.0.0.1", 9)

    with pytest.raises(ConnectionRefusedError):
        asyncio.run(connect())
    assert no_network.blocked == []


def test_startup_and_shutdown_work_offline(no_network: NetworkGuard, settings: Settings) -> None:
    assert settings.app_env == "test"
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert no_network.blocked == []


def test_startup_with_a_database_url_stays_offline(
    no_network: NetworkGuard, settings: Settings
) -> None:
    """The engine is built at startup but connects only on the first query (step 5)."""
    online = settings.model_copy(
        update={
            "blastproof_database_url": SecretStr(
                "postgresql+asyncpg://blastproof_app:x@203.0.113.10:5432/blastproof"
            )
        }
    )
    with TestClient(create_app(online)) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert no_network.blocked == []
