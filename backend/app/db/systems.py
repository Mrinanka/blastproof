"""Queries on `systems`: the company databases Blastproof protects."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import System


async def get_or_create_system(
    session: AsyncSession,
    *,
    name: str,
    connection_profile: str,
    neon_project_id: str | None,
    neon_parent_branch_id: str | None,
) -> System:
    """The system with this name, created with these values if it does not exist yet.

    ON CONFLICT DO NOTHING makes two first calls at once safe: one inserts, both then read it.
    An existing row is returned unchanged; the caller compares its profile.
    """
    await session.execute(
        insert(System)
        .values(
            name=name,
            connection_profile=connection_profile,
            neon_project_id=neon_project_id,
            neon_parent_branch_id=neon_parent_branch_id,
        )
        .on_conflict_do_nothing(index_elements=[System.name])
    )
    return (await session.execute(select(System).where(System.name == name))).scalar_one()
