"""Writes to `hook_events`, one row per Claude Code hook call (kept out of `actions`)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HookEvent


async def insert_hook_event(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    tool: str,
    summary: str,
    decision: str,
    reason: str,
    rule: str | None,
) -> None:
    session.add(
        HookEvent(
            user_id=user_id,
            agent_id=agent_id,
            tool=tool,
            summary=summary,
            decision=decision,
            reason=reason,
            rule=rule,
        )
    )
    await session.flush()
