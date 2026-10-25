"""Connections to the company database (steps 6 and 10).

Reads use `blastproof_reader`, one connection per call, no pool (D-109): matches D-019 for the
`local` profile, and a pool for `neon` can come later. Shadow runs and commits use
`blastproof_writer` (`connect_target`, `connect_writer`). The URL is picked by
COMPANY_CONNECTION_PROFILE and never logged.

On the `local` profile a shadow run clones `company_main`, which needs every other session on it
gone. Reads therefore hold a shared advisory lock and a clone holds it exclusively
(`company_main_lock`); advisory locks are per database, so the lock lives on the server's
`postgres` database, not on `company_main`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import structlog

from app.config import Settings

if TYPE_CHECKING:
    from app.services.neon import ShadowTarget

STATEMENT_TIMEOUT = "5s"
CONNECT_TIMEOUT_SECONDS = 10
# The session settings every read runs with: UTC timestamps, exact floats, the 5 s read limit.
SERVER_SETTINGS = {
    "TimeZone": "UTC",
    "extra_float_digits": "1",
    "statement_timeout": STATEMENT_TIMEOUT,
}
# Every connection that hashes rows pins what `row_to_json` output depends on, so a hash taken on a
# shadow copy equals one taken on the real database (D-136). Reads keep SERVER_SETTINGS.
HASH_SESSION_SETTINGS = {
    "TimeZone": "UTC",
    "extra_float_digits": "1",
    "DateStyle": "ISO, MDY",
    "IntervalStyle": "postgres",
    "bytea_output": "hex",
}
LOCK_KEY = 0x424C5354  # "BLST": the one lock a clone of company_main takes
LOCK_WAIT_SECONDS = 30

logger = structlog.get_logger()


class CompanyDbUnavailableError(Exception):
    """Not configured, unreachable or refused the login. The message is a reason code only."""


def reader_url(settings: Settings) -> str | None:
    url = (
        settings.local_company_reader_url
        if settings.company_connection_profile == "local"
        else settings.company_db_reader_url
    )
    return url.get_secret_value() if url is not None else None


@asynccontextmanager
async def connect_reader(settings: Settings) -> AsyncIterator[asyncpg.Connection]:
    url = reader_url(settings)
    if url is None:
        logger.error("company_db_not_configured", profile=settings.company_connection_profile)
        raise CompanyDbUnavailableError("not_configured")
    async with (
        company_main_lock(settings, shared=True),
        # The exception text can hold the host or user name; only its class is logged.
        open_connection(dsn=url, server_settings=SERVER_SETTINGS) as conn,
    ):
        yield conn


def writer_url(settings: Settings) -> str | None:
    url = (
        settings.local_company_writer_url
        if settings.company_connection_profile == "local"
        else settings.company_db_writer_url
    )
    return url.get_secret_value() if url is not None else None


def with_database(url: str, database: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{database}"))


@asynccontextmanager
async def open_connection(**connect_args: object) -> AsyncIterator[asyncpg.Connection]:
    try:
        conn = await asyncpg.connect(timeout=CONNECT_TIMEOUT_SECONDS, **connect_args)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("company_db_unavailable", reason_code=type(exc).__name__)
        raise CompanyDbUnavailableError(type(exc).__name__) from None
    try:
        yield conn
    finally:
        try:
            await conn.close(timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception:
            conn.terminate()


@asynccontextmanager
async def connect_writer(settings: Settings) -> AsyncIterator[asyncpg.Connection]:
    """A direct `blastproof_writer` connection with the hash settings (the commit, step 16)."""
    url = writer_url(settings)
    if url is None:
        logger.error("company_db_not_configured", profile=settings.company_connection_profile)
        raise CompanyDbUnavailableError("not_configured")
    async with open_connection(dsn=url, server_settings=HASH_SESSION_SETTINGS) as conn:
        yield conn


@asynccontextmanager
async def connect_target(target: "ShadowTarget") -> AsyncIterator[asyncpg.Connection]:
    """A `blastproof_writer` connection to one shadow copy, with the hash settings."""
    async with open_connection(
        **target.connect_kwargs, server_settings=HASH_SESSION_SETTINGS
    ) as conn:
        yield conn


@asynccontextmanager
async def advisory_lock(url: str, *, shared: bool) -> AsyncIterator[None]:
    """Hold the clone lock on the server `url` points at (its `postgres` database) while inside.

    Closing the connection releases it, so a crashed process never leaves the lock held.
    """
    function = "pg_advisory_lock_shared" if shared else "pg_advisory_lock"
    async with open_connection(dsn=with_database(url, "postgres")) as conn:
        try:
            await conn.execute(f"SET lock_timeout = '{LOCK_WAIT_SECONDS}s'")
            await conn.execute(f"SELECT {function}({LOCK_KEY})")
        except Exception as exc:
            logger.error("company_db_lock_failed", reason_code=type(exc).__name__)
            raise CompanyDbUnavailableError("lock_failed") from None
        yield


@asynccontextmanager
async def company_main_lock(settings: Settings, *, shared: bool) -> AsyncIterator[None]:
    """The lock between reads of `company_main` (shared) and clones of it (exclusive).

    Only the `local` profile clones `company_main`; Neon shadow runs use branches, so no lock.
    """
    url = reader_url(settings)
    if settings.company_connection_profile != "local" or url is None:
        yield
        return
    async with advisory_lock(url, shared=shared):
        yield
