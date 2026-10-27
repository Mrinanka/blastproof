"""Queries on `agents`. Only a key's SHA-256 hash is stored, so lookups go by hash."""

import uuid

from sqlalchemy import select
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
