"""The one "main" company system per deployment (D-102).

`actions.system_id` is required, but the bootstrap that registers systems is step 20. Until then
the first tool call creates the row named `company` with the configured profile; step 20 reuses it.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import systems as systems_db
from app.db.models import System

MAIN_SYSTEM_NAME = "company"

logger = structlog.get_logger()


class SystemProfileMismatchError(Exception):
    """The stored main system uses another connection profile than this deployment."""


async def ensure_main_system(session: AsyncSession, settings: Settings) -> System:
    """Get or create the main system. Fails closed if its stored profile differs from config.

    Actions recorded against one company database must never be attributed to another.
    """
    system = await systems_db.get_or_create_system(
        session,
        name=MAIN_SYSTEM_NAME,
        connection_profile=settings.company_connection_profile,
        neon_project_id=settings.neon_project_id,
        neon_parent_branch_id=settings.neon_parent_branch_id,
    )
    if system.connection_profile != settings.company_connection_profile:
        logger.error(
            "system_profile_mismatch",
            stored=system.connection_profile,
            configured=settings.company_connection_profile,
        )
        raise SystemProfileMismatchError("system_profile_mismatch")
    return system
