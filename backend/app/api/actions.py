import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.clock import SystemClock
from app.core.errors import ApiError, ErrorResponse
from app.db import actions as actions_db
from app.db.session import get_session
from app.schemas.actions import ActionDetail, ActionFilters, ActionPage
from app.services import actions as actions_service
from app.services.policy import Policy

router = APIRouter(prefix="/v1/actions", tags=["actions"])


def get_policy(request: Request) -> Policy:
    return request.app.state.policy


@router.get(
    "",
    response_model=ActionPage,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_actions(
    filters: Annotated[ActionFilters, Query()],
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActionPage:
    """Your actions, newest first (admins see everyone's). Filters combine with AND."""
    rows, total = await actions_db.list_visible_actions(
        session,
        user.id,
        is_admin=user.is_admin,
        filters=actions_db.ListFilters(
            statuses=tuple(filters.status),
            agent_id=filters.agent_id,
            table=filters.table,
            created_from=filters.from_,
            created_to=filters.to,
            newest_first=filters.order == "newest",
            limit=filters.limit,
            offset=filters.offset,
        ),
    )
    return ActionPage(
        items=[actions_service.build_summary(row) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get(
    "/{action_id}",
    response_model=ActionDetail,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_action(
    action_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    policy: Annotated[Policy, Depends(get_policy)],
) -> ActionDetail:
    """One action in full. Unknown, malformed and other people's IDs all answer 404."""
    try:
        parsed = uuid.UUID(action_id)
    except ValueError:
        raise ApiError(404, "not_found", "No action was found.") from None
    structlog.contextvars.bind_contextvars(action_id=str(parsed))
    rows = await actions_db.get_action_detail_rows(session, parsed, user.id, is_admin=user.is_admin)
    if rows is None:
        raise ApiError(404, "not_found", "No action was found.")
    return await actions_service.build_detail(rows, policy, SystemClock().now())
