"""The running app's connection to Blastproof's own database, as `blastproof_app`.

The engine connects lazily (startup stays offline, TEST-07). Every new connection checks that it
is logged in as `blastproof_app` and refuses anything else, so a misconfigured URL pointing at the
owner or a superuser can never serve requests (SEC-09; the migrator role is for migrations only).
"""

from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.errors import ApiError

APP_ROLE = "blastproof_app"

logger = structlog.get_logger()


class WrongAppRoleError(RuntimeError):
    pass


def check_app_role(current_user: str) -> None:
    if current_user != APP_ROLE:
        logger.error("wrong_app_role", expected=APP_ROLE, actual=current_user)
        raise WrongAppRoleError(
            f"The app must connect as {APP_ROLE}, not {current_user}."
            " Point BLASTPROOF_DATABASE_URL at the app role."
        )


def make_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: Neon closes idle connections; a dead pooled one is replaced, not used.
    engine = create_async_engine(url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _refuse_other_roles(dbapi_connection: Any, _record: Any) -> None:
        role = dbapi_connection.run_async(lambda conn: conn.fetchval("SELECT current_user"))
        check_app_role(role)

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed (and rolled back if open) at the end."""
    sessionmaker: async_sessionmaker[AsyncSession] | None = request.app.state.sessionmaker
    if sessionmaker is None:
        logger.error("database_not_configured")
        raise ApiError(503, "unavailable", "Blastproof's database is not configured.")
    async with sessionmaker() as session:
        yield session


async def get_write_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A second session for routes that write after `get_current_user` has read.

    The sign-in check already began a transaction on the request's own session (`get_session`), and
    a service that opens its own with `session.begin()` would fail on that one. FastAPI caches a
    dependency per function, so this one yields a session of its own.
    """
    async for session in get_session(request):
        yield session
