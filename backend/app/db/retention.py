"""Deletes working data older than the retention window (SEC-16).

`audit_events` is never touched: it is the permanent record (the app role could not delete from it
anyway, D-083).
"""

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BeforeImage, HookEvent, LoginAttempt, PreviewRow

RETAINED_TABLES = (PreviewRow, BeforeImage, HookEvent, LoginAttempt)


async def delete_older_than(session: AsyncSession, cutoff: datetime) -> dict[str, int]:
    """Delete rows created before `cutoff`; returns how many went from each table."""
    deleted: dict[str, int] = {}
    for model in RETAINED_TABLES:
        result = await session.execute(delete(model).where(model.created_at < cutoff))
        deleted[model.__tablename__] = result.rowcount  # type: ignore[attr-defined]
    return deleted
