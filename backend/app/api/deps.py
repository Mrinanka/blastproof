"""Dependencies shared by the dashboard routes."""

import structlog
from fastapi import Request
from pydantic import BaseModel, Field

from app.core.clock import Clock, SystemClock
from app.core.errors import ApiError
from app.services.knowledge import KnowledgeService

logger = structlog.get_logger()


class PageParams(BaseModel):
    """`limit` and `offset` of every list route (CONV-04). Unknown parameters are ignored."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def get_clock() -> Clock:
    # A dependency, so tests can override it with a FrozenClock.
    return SystemClock()


def get_knowledge(request: Request) -> KnowledgeService:
    knowledge: KnowledgeService | None = request.app.state.knowledge
    if knowledge is None:
        logger.error("knowledge_not_configured")
        raise ApiError(503, "unavailable", "Knowledge is not configured.")
    return knowledge
