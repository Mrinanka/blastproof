"""Login with rate limiting, and creating users and agents (step 5).

Login limits (SEC-06): at most 5 failed logins per email and IP in 15 minutes, and 20 per email
from any IP in an hour, so rotating IPs does not help. Over a limit, the attempt is refused before
the password is checked (even a correct one) and is not recorded, so a lockout ends exactly when
the window passes. Unknown emails are recorded too, so guessing emails is limited the same way.
"""

import asyncio
import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import NewAgentKey, generate_agent_key, hash_password, verify_password
from app.db import agents as agents_db
from app.db import login_attempts as attempts_db
from app.db import users as users_db
from app.db.enums import UserRole
from app.db.login_attempts import FailureWindow
from app.db.models import Agent, User

MAX_FAILURES_PER_EMAIL_AND_IP = 5
EMAIL_AND_IP_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_EMAIL = 20
EMAIL_WINDOW = timedelta(hours=1)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 100
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# A peer address that is not an IP (e.g. a test client) shares this bucket; it is still limited.
UNKNOWN_IP = "0.0.0.0"

logger = structlog.get_logger()


class InvalidCredentialsError(Exception):
    """Wrong password, unknown email or inactive user: the client is never told which."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many failed logins")
        self.retry_after_seconds = retry_after_seconds


class AccountError(ValueError):
    """A create-user or create-agent input problem; the message is safe to show."""


def normalise_email(email: str) -> str:
    return email.strip().lower()


def client_ip(host: str | None) -> str:
    """The peer address as an IP string, or UNKNOWN_IP. Proxy headers are not trusted (yet)."""
    if host is None:
        return UNKNOWN_IP
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return UNKNOWN_IP


def _seconds_until_free(window: FailureWindow, length: timedelta, now: datetime) -> int:
    # The lock keeps counts at most at the limit, so the window frees when its oldest entry leaves.
    assert window.oldest is not None
    return max(1, math.ceil((window.oldest + length - now).total_seconds()))


async def authenticate(session: AsyncSession, email: str, password: str, ip: str) -> User:
    """The user for these credentials, or InvalidCredentialsError / TooManyAttemptsError.

    One transaction per attempt, holding a per-email advisory lock, so parallel attempts are
    counted one at a time. A failed attempt is committed before the error is raised.
    """
    email = normalise_email(email)
    async with session.begin():
        await attempts_db.lock_email(session, email)
        failures = await attempts_db.recent_failures(
            session, email, ip, ip_window=EMAIL_AND_IP_WINDOW, email_window=EMAIL_WINDOW
        )
        retry_after = []
        if failures.for_email_and_ip.count >= MAX_FAILURES_PER_EMAIL_AND_IP:
            retry_after.append(
                _seconds_until_free(failures.for_email_and_ip, EMAIL_AND_IP_WINDOW, failures.now)
            )
        if failures.for_email.count >= MAX_FAILURES_PER_EMAIL:
            retry_after.append(_seconds_until_free(failures.for_email, EMAIL_WINDOW, failures.now))
        if retry_after:
            logger.warning("login_refused", reason="too_many_failures", ip=ip)
            raise TooManyAttemptsError(max(retry_after))

        user = await users_db.get_user_by_email(session, email)
        # Argon2 is CPU-bound: keep it off the event loop. Unknown emails still pay for a verify.
        matches, new_hash = await asyncio.to_thread(
            verify_password, password, user.password_hash if user else None
        )
        succeeded = user is not None and matches and user.is_active
        await attempts_db.record_attempt(session, email, ip, succeeded=succeeded)
        if succeeded and new_hash is not None:
            user.password_hash = new_hash  # type: ignore[union-attr]

    if not succeeded:
        reason = "unknown_email" if user is None else "inactive" if matches else "wrong_password"
        logger.info("login_failed", reason=reason, ip=ip)
        raise InvalidCredentialsError()
    assert user is not None
    logger.info("login_succeeded", user_id=str(user.id), ip=ip)
    return user


def _check_name(value: str, what: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_NAME_LENGTH:
        raise AccountError(f"{what} must be 1 to {MAX_NAME_LENGTH} characters.")
    return value


async def create_user(
    session: AsyncSession, *, email: str, display_name: str, role: UserRole, password: str
) -> User:
    email = normalise_email(email)
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL_PATTERN.fullmatch(email):
        raise AccountError("That is not a valid email address.")
    display_name = _check_name(display_name, "The display name")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise AccountError(
            f"The password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters."
        )
    async with session.begin():
        if await users_db.get_user_by_email(session, email) is not None:
            raise AccountError("A user with that email already exists.")
        password_hash = await asyncio.to_thread(hash_password, password)
        user = await users_db.insert_user(
            session,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            role=role.value,
        )
    logger.info("user_created", user_id=str(user.id), role=role.value)
    return user


@dataclass(frozen=True)
class CreatedAgent:
    agent: Agent
    key: NewAgentKey  # the full key is in here; show it once and drop it


async def create_agent(session: AsyncSession, *, owner_email: str, name: str) -> CreatedAgent:
    name = _check_name(name, "The agent name")
    async with session.begin():
        owner = await users_db.get_user_by_email(session, normalise_email(owner_email))
        if owner is None or not owner.is_active:
            raise AccountError("No active user has that email.")
        key = generate_agent_key()
        agent = await agents_db.insert_agent(
            session,
            owner_id=owner.id,
            name=name,
            key_hash=key.key_hash,
            key_prefix=key.key_prefix,
        )
    logger.info("agent_created", agent_id=str(agent.id), user_id=str(owner.id))
    return CreatedAgent(agent=agent, key=key)
