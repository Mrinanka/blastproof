import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, get_knowledge
from app.config import Settings
from app.core.auth import CurrentUser, get_app_settings, get_current_user, require_admin
from app.core.errors import ApiError, ErrorResponse
from app.db import systems as systems_db
from app.db.enums import ConnectionProfile, SystemKind
from app.db.models import System
from app.db.session import get_session
from app.schemas.systems import ScanResult, SystemPage, SystemView
from app.services.company_db import CompanyDbUnavailableError
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/v1/systems", tags=["systems"])
logger = structlog.get_logger()


def _view(system: System) -> SystemView:
    return SystemView(
        id=system.id,
        name=system.name,
        kind=SystemKind(system.kind),
        connection_profile=ConnectionProfile(system.connection_profile),
    )


@router.get(
    "",
    response_model=SystemPage,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_systems(
    page: Annotated[PageParams, Query()],
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SystemPage:
    """The company databases Blastproof protects, by name."""
    rows, total = await systems_db.list_systems(session, limit=page.limit, offset=page.offset)
    return SystemPage(
        items=[_view(row) for row in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.post(
    "/{system_id}/scan",
    response_model=ScanResult,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
)
async def scan_system(
    system_id: str,
    _admin: Annotated[CurrentUser, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge)],
) -> ScanResult:
    """Re-read the tables of one system and rebuild the schema knowledge (admins only)."""
    try:
        parsed = uuid.UUID(system_id)
    except ValueError:
        raise ApiError(404, "not_found", "No system was found.") from None
    system = await systems_db.get_system(session, parsed)
    if system is None:
        raise ApiError(404, "not_found", "No system was found.")
    try:
        # Through this system's own reader, never the other profile's (no fallback).
        chunks = await knowledge.scan(settings, system.connection_profile)
    except CompanyDbUnavailableError:
        raise ApiError(503, "unavailable", "The company database is not reachable.") from None
    except Exception as exc:
        logger.error("system_scan_failed", reason_code=type(exc).__name__)
        raise ApiError(503, "unavailable", "The schema could not be scanned.") from None
    return ScanResult(system_id=system.id, source="schema_scan", chunks=chunks)
