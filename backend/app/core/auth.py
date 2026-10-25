"""Who is calling: dashboard users (JWT) and agents (API key). FastAPI dependencies.

Every failure is a 401 with one generic message per kind; the reason goes to the logs only.
`authenticate_agent_key` is a plain function so the `/mcp` middleware (app/mcp/auth.py) can call
it too (FastAPI dependencies do not run for the SDK's route).
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ApiError
from app.core.security import (
    InvalidTokenError,
    decode_access_token,
    hash_agent_key,
    parse_agent_key,
)
from app.db import agents as agents_db
from app.db import users as users_db
from app.db.enums import UserRole
from app.db.session import get_session

logger = structlog.get_logger()

_BEARER = {"WWW-Authenticate": "Bearer"}
# Shared with the /mcp middleware, so both doors refuse a bad key the same way.
INVALID_AGENT_KEY_MESSAGE = "The agent key is invalid or deactivated."

user_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="UserToken",
    bearerFormat="JWT",
    description="The token from POST /v1/auth/login.",
)
agent_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AgentKey",
    description="An agent's API key (bp_...).",
)


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN


@dataclass(frozen=True)
class CurrentAgent:
    agent_id: uuid.UUID
    agent_name: str
    # The owner's id is what every action stores (ownership, ARCH-13).
    owner_id: uuid.UUID


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def jwt_secret(settings: Settings) -> str:
    if settings.jwt_secret is None:
        logger.error("jwt_secret_not_configured")
        raise ApiError(503, "unavailable", "Dashboard login is not configured.")
    return settings.jwt_secret.get_secret_value()


def _not_authenticated(reason: str) -> ApiError:
    logger.info("auth_failed", kind="user", reason=reason)
    return ApiError(401, "not_authenticated", "Sign in again.", headers=_BEARER)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(user_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CurrentUser:
    """The signed-in user. Inactive users and tokens with an old token_version get 401 (SEC-05)."""
    if credentials is None:
        raise _not_authenticated("missing")
    secret = jwt_secret(settings)
    try:
        claims = decode_access_token(credentials.credentials, secret)
    except InvalidTokenError as exc:
        raise _not_authenticated(f"invalid_token:{exc}") from None

    user = await users_db.get_user(session, claims.user_id)
    if user is None:
        raise _not_authenticated("unknown_user")
    if not user.is_active:
        raise _not_authenticated("inactive")
    if user.token_version != claims.token_version:
        raise _not_authenticated("revoked")
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return CurrentUser(
        id=user.id, email=user.email, display_name=user.display_name, role=UserRole(user.role)
    )


async def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    """For admin-only routes: members get 403 (SEC-07)."""
    if not user.is_admin:
        logger.info("forbidden", reason="admin_only")
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


async def authenticate_agent_key(session: AsyncSession, key: str | None) -> CurrentAgent | None:
    """The agent for this key, or None for a missing, unknown or deactivated key (SEC-33).

    An agent whose owner has been deactivated is refused too.
    """
    if key is None:
        reason = "missing"
    elif parse_agent_key(key) is None:
        reason = "malformed"
    else:
        found = await agents_db.get_agent_and_owner_by_key_hash(session, hash_agent_key(key))
        if found is None:
            reason = "unknown_key"
        elif not found[0].is_active:
            reason = "agent_inactive"
        elif not found[1].is_active:
            reason = "owner_inactive"
        else:
            agent = found[0]
            structlog.contextvars.bind_contextvars(
                agent_id=str(agent.id), user_id=str(agent.owner_id)
            )
            return CurrentAgent(agent_id=agent.id, agent_name=agent.name, owner_id=agent.owner_id)
    logger.info("auth_failed", kind="agent", reason=reason)
    return None


async def get_current_agent(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(agent_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentAgent:
    agent = await authenticate_agent_key(session, credentials.credentials if credentials else None)
    if agent is None:
        raise ApiError(401, "invalid_agent_key", INVALID_AGENT_KEY_MESSAGE, _BEARER)
    return agent
