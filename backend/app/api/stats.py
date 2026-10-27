from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_clock
from app.core.auth import CurrentUser, get_current_user
from app.core.clock import Clock
from app.core.errors import ErrorResponse
from app.db.session import get_session
from app.schemas.stats import StatsResponse
from app.services import stats as stats_service

router = APIRouter(prefix="/v1/stats", tags=["stats"])


@router.get("", response_model=StatsResponse, responses={401: {"model": ErrorResponse}})
async def get_stats(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> StatsResponse:
    """Counts for the dashboard: all time, and today (UTC). Yours, or everyone's for an admin."""
    return await stats_service.build_stats(session, user, clock)
