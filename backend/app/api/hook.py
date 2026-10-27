from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentAgent, get_current_agent
from app.core.errors import ErrorResponse
from app.db.session import get_session
from app.schemas.hook import HookDecisionResponse, HookRequest
from app.services import hooks as hooks_service

router = APIRouter(prefix="/v1/hook", tags=["hook"])


@router.post(
    "/pre-tool-use",
    response_model=HookDecisionResponse,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def pre_tool_use(
    body: HookRequest,
    agent: Annotated[CurrentAgent, Depends(get_current_agent)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HookDecisionResponse:
    """Claude Code's PreToolUse hook asks here before every shell, edit and read tool call."""
    return await hooks_service.check_hook_call(session, agent, body)
