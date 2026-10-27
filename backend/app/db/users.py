"""Queries on `users`. Emails are stored lower case and unique ignoring case (D-084)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    return result.scalar_one_or_none()


async def insert_user(
    session: AsyncSession, *, email: str, display_name: str, password_hash: str, role: str
) -> User:
    user = User(email=email, display_name=display_name, password_hash=password_hash, role=role)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def list_users(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[User], int]:
    """Newest first, and the total before paging."""
    total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    rows = (
        await session.execute(
            select(User)
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return list(rows), total
