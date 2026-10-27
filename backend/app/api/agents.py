import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams
from app.config import Settings
from app.core.auth import CurrentUser, get_app_settings, get_current_user
from app.core.errors import ApiError, ErrorResponse
from app.db import agents as agents_db
from app.db.session import get_session, get_write_session
from app.schemas.agents import AgentCreated, AgentPage, AgentView, CreateAgentRequest
from app.services import agent_admin

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.get(
    "",
    response_model=AgentPage,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_agents(
    page: Annotated[PageParams, Query()],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentPage:
    """Your agents, newest first (admins see everyone's). Keys are never shown here."""
    rows, total = await agents_db.list_visible_agents(
        session, user.id, is_admin=user.is_admin, limit=page.limit, offset=page.offset
    )
    return AgentPage(
        items=[agent_admin.agent_view(agent, owner) for agent, owner in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=AgentCreated,
    status_code=201,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_agent(
    body: CreateAgentRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_write_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AgentCreated:
    """Make an agent that you own. The response holds its key: this is the only time it is shown."""
    response.headers["Cache-Control"] = "no-store"
    return await agent_admin.create_agent(session, settings, user, body.name)


@router.post(
    "/{agent_id}/deactivate",
    response_model=AgentView,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def deactivate_agent(
    agent_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_write_session)],
) -> AgentView:
    """Turn an agent's key off (its owner or an admin). Doing it twice is fine."""
    try:
        parsed = uuid.UUID(agent_id)
    except ValueError:
        raise ApiError(404, "not_found", "No agent was found.") from None
    return await agent_admin.deactivate_agent(session, parsed, user)
