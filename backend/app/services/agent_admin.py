"""Agents for the dashboard: create, deactivate and the lines that connect Claude Code."""

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.auth import CurrentUser
from app.core.errors import ApiError
from app.db import agents as agents_db
from app.db.models import Agent, User
from app.schemas.actions import UserRef
from app.schemas.agents import AgentCreated, AgentView
from app.services import auth as auth_service

logger = structlog.get_logger()

_NOT_FOUND = "No agent was found."


def agent_view(agent: Agent, owner: User) -> AgentView:
    return AgentView(
        id=agent.id,
        name=agent.name,
        key_prefix=agent.key_prefix,
        is_active=agent.is_active,
        created_at=agent.created_at,
        owner=UserRef(id=owner.id, display_name=owner.display_name),
    )


def mcp_url(settings: Settings) -> str:
    return f"{settings.public_base_url.rstrip('/')}/mcp"


def setup_lines(settings: Settings, key: str) -> tuple[str, str, str]:
    """The MCP address, the `claude mcp add` command and the hook's environment line."""
    url = mcp_url(settings)
    return (
        url,
        f'claude mcp add --transport http blastproof {url} --header "Authorization: Bearer {key}"',
        f"BLASTPROOF_AGENT_KEY={key}",
    )


async def create_agent(
    session: AsyncSession, settings: Settings, user: CurrentUser, name: str
) -> AgentCreated:
    try:
        created = await auth_service.create_agent_for_owner(session, owner_id=user.id, name=name)
    except auth_service.AccountError as exc:
        raise ApiError(422, "invalid_account", str(exc)) from None
    url, add_line, env_line = setup_lines(settings, created.key.key)
    view = AgentView(
        id=created.agent.id,
        name=created.agent.name,
        key_prefix=created.agent.key_prefix,
        is_active=created.agent.is_active,
        created_at=created.agent.created_at,
        owner=UserRef(id=user.id, display_name=user.display_name),
    )
    return AgentCreated(
        **view.model_dump(),
        key=created.key.key,
        mcp_url=url,
        claude_mcp_add=add_line,
        agent_key_env=env_line,
    )


async def deactivate_agent(
    session: AsyncSession, agent_id: uuid.UUID, user: CurrentUser
) -> AgentView:
    """The owner or an admin; anyone else gets the same 404 as for an unknown agent."""
    async with session.begin():
        found = await agents_db.get_visible_agent(
            session, agent_id, user.id, is_admin=user.is_admin
        )
        if found is None:
            raise ApiError(404, "not_found", _NOT_FOUND)
        agent, owner = found
        await agents_db.deactivate(session, agent.id)
    logger.info("agent_deactivated", agent_id=str(agent.id), user_id=str(user.id))
    agent.is_active = False
    return agent_view(agent, owner)
