"""Connections to the company database as `blastproof_reader` (step 6).

One connection per call, no pool (D-109): matches D-019 for the `local` profile, and a pool for
`neon` can come later. The URL is picked by COMPANY_CONNECTION_PROFILE and never logged.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import structlog

from app.config import Settings

STATEMENT_TIMEOUT = "5s"
CONNECT_TIMEOUT_SECONDS = 10
# The session settings every read runs with: UTC timestamps, exact floats, the 5 s read limit.
SERVER_SETTINGS = {
    "TimeZone": "UTC",
    "extra_float_digits": "1",
    "statement_timeout": STATEMENT_TIMEOUT,
}

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
    try:
        conn = await asyncpg.connect(
            url, timeout=CONNECT_TIMEOUT_SECONDS, server_settings=SERVER_SETTINGS
        )
    except Exception as exc:
        # The exception text can hold the host or user name; only its class is logged.
        logger.error("company_db_unavailable", reason_code=type(exc).__name__)
        raise CompanyDbUnavailableError(type(exc).__name__) from None
    try:
        yield conn
    finally:
        try:
            await conn.close(timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception:
            conn.terminate()  # a broken connection cannot close politely
