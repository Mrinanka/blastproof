"""Queries on `login_attempts`, the record login rate limiting counts from (SEC-06).

Windows are measured with the database's `now()`, so every count uses one clock.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LoginAttempt

# First key of the two-key advisory lock form; that key space never overlaps the one-key form.
LOGIN_LOCK_CLASS = 0x6C6F67  # "log"


def _email_lock_key(email: str) -> int:
    # A signed 32-bit int from the email's hash: the second key of pg_advisory_xact_lock(int, int).
    return int.from_bytes(hashlib.sha256(email.encode()).digest()[:4], "big", signed=True)


async def lock_email(session: AsyncSession, email: str) -> None:
    """Serialise login attempts for one email until the transaction ends.

    Without it, parallel requests could all count 4 failures and all be let through.
    """
    await session.execute(
        select(func.pg_advisory_xact_lock(LOGIN_LOCK_CLASS, _email_lock_key(email)))
    )


@dataclass(frozen=True)
class FailureWindow:
    count: int
    # When the oldest failure in the window was made (None when there are none).
    oldest: datetime | None


@dataclass(frozen=True)
class RecentFailures:
    for_email_and_ip: FailureWindow
    for_email: FailureWindow
    now: datetime


async def recent_failures(
    session: AsyncSession,
    email: str,
    ip: str,
    *,
    ip_window: timedelta,
    email_window: timedelta,
) -> RecentFailures:
    now = func.now()
    in_ip_window = and_(LoginAttempt.ip == ip, LoginAttempt.created_at > now - ip_window)
    in_email_window = LoginAttempt.created_at > now - email_window
    result = await session.execute(
        select(
            func.count().filter(in_ip_window),
            func.min(LoginAttempt.created_at).filter(in_ip_window),
            func.count().filter(in_email_window),
            func.min(LoginAttempt.created_at).filter(in_email_window),
            now,
        ).where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at > now - max(ip_window, email_window),
        )
    )
    ip_count, ip_oldest, email_count, email_oldest, db_now = result.one()
    return RecentFailures(
        for_email_and_ip=FailureWindow(ip_count, ip_oldest),
        for_email=FailureWindow(email_count, email_oldest),
        now=db_now,
    )


async def record_attempt(session: AsyncSession, email: str, ip: str, *, succeeded: bool) -> None:
    session.add(LoginAttempt(email=email, ip=ip, succeeded=succeeded))
    await session.flush()
