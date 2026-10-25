"""Readiness (CONV-03): can this process serve calls right now?

Checks Blastproof's database and the company database, each with a 3 s timeout, at the same time.
The Neon API check comes with the Neon client in step 10 (D-105). A failure names the checks
that failed, never the exception text (which can hold a host or user name).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.config import Settings
from app.core.auth import get_app_settings
from app.core.errors import ApiError, ErrorResponse
from app.schemas.ready import ReadyResponse
from app.services import company_db

CHECK_TIMEOUT_SECONDS = 3

router = APIRouter(tags=["health"])
logger = structlog.get_logger()


async def _check(probe: Callable[[], Awaitable[None]]) -> str | None:
    """None if the probe passed in time, else the exception's class name (for the log)."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await probe()
    except Exception as exc:
        return type(exc).__name__
    return None


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ErrorResponse}})
async def ready(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> ReadyResponse:
    """Readiness: Blastproof's database and the company database are reachable."""

    async def blastproof_db() -> None:
        sessionmaker = request.app.state.sessionmaker
        if sessionmaker is None:
            raise RuntimeError("not configured")
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))

    async def company() -> None:
        async with company_db.connect_reader(settings) as conn:
            await conn.fetchval("SELECT 1")

    names = ["blastproof_db", "company_db"]
    results = await asyncio.gather(_check(blastproof_db), _check(company))
    failed = {name: reason for name, reason in zip(names, results, strict=True) if reason}
    if failed:
        logger.warning("not_ready", reason_codes=failed)
        raise ApiError(503, "not_ready", f"Not ready: {', '.join(failed)}.")
    return ReadyResponse(status="ready", checks=dict.fromkeys(names, "ok"))
