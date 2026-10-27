"""Queries on `agents`. Only a key's SHA-256 hash is stored, so lookups go by hash."""

import uuid

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, User


async def get_agent_and_owner_by_key_hash(
    session: AsyncSession, key_hash: str
) -> tuple[Agent, User] | None:
    result = await session.execute(
        select(Agent, User)
        .join(User, Agent.owner_id == User.id)
        .where(Agent.api_key_hash == key_hash)
    )
    row = result.one_or_none()
    return None if row is None else (row[0], row[1])


async def get_agent_by_owner_and_name(
    session: AsyncSession, owner_id: uuid.UUID, name: str
) -> Agent | None:
    result = await session.execute(
        select(Agent).where(Agent.owner_id == owner_id, Agent.name == name)
    )
    return result.scalars().first()


async def insert_agent(
    session: AsyncSession, *, owner_id: uuid.UUID, name: str, key_hash: str, key_prefix: str
) -> Agent:
    agent = Agent(owner_id=owner_id, name=name, api_key_hash=key_hash, key_prefix=key_prefix)
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


def _visible_agents(user_id: uuid.UUID, *, is_admin: bool) -> Select[tuple[Agent, User]]:
    """Agents with their owners: your own, or everyone's for an admin."""
    query = select(Agent, User).join(User, Agent.owner_id == User.id)
    return query if is_admin else query.where(Agent.owner_id == user_id)


async def list_visible_agents(
    session: AsyncSession, user_id: uuid.UUID, *, is_admin: bool, limit: int, offset: int
) -> tuple[list[tuple[Agent, User]], int]:
    """Newest first, and the total before paging."""
    query = _visible_agents(user_id, is_admin=is_admin)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await session.execute(
            query.order_by(Agent.created_at.desc(), Agent.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    return [(row[0], row[1]) for row in rows], total


async def get_visible_agent(
    session: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID, *, is_admin: bool
) -> tuple[Agent, User] | None:
    """None for an unknown agent and for someone else's (the route answers 404 for both)."""
    row = (
        await session.execute(
            _visible_agents(user_id, is_admin=is_admin).where(Agent.id == agent_id)
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def deactivate(session: AsyncSession, agent_id: uuid.UUID) -> None:
    """Idempotent: a second call changes nothing."""
    await session.execute(update(Agent).where(Agent.id == agent_id).values(is_active=False))
