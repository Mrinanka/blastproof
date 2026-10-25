"""Queries on `actions` and their audit events.

Every query that reads actions goes through `visible_actions` (CLAUDE.md, ARCH-13): it filters by
the owner's `user_id` unless admin widening is on. Agents always call it with `is_admin=False`
(D-055), so an admin's agent sees only its owner's actions.
"""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ActionStatus, AuditEventType
from app.db.models import Action, AuditEvent


def visible_actions(user_id: uuid.UUID, *, is_admin: bool) -> Select[tuple[Action]]:
    query = select(Action)
    return query if is_admin else query.where(Action.user_id == user_id)


async def get_visible_action(
    session: AsyncSession, action_id: uuid.UUID, user_id: uuid.UUID, *, is_admin: bool
) -> Action | None:
    """The action if this user may see it; None for an unknown or someone else's action."""
    query = visible_actions(user_id, is_admin=is_admin).where(Action.id == action_id)
    return (await session.execute(query)).scalar_one_or_none()


async def create_action(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    system_id: uuid.UUID,
    tool: str,
    intent: str | None = None,
    sql: str | None = None,
    statement_type: str | None = None,
    tables: list[str] | None = None,
) -> Action:
    """A new action in `received`, with its `received` audit event."""
    action = Action(
        user_id=user_id,
        agent_id=agent_id,
        system_id=system_id,
        tool=tool,
        intent=intent,
        sql=sql,
        statement_type=statement_type,
        tables=tables or [],
        status=ActionStatus.RECEIVED.value,
    )
    session.add(action)
    await session.flush()
    session.add(AuditEvent(action_id=action.id, event_type=AuditEventType.RECEIVED.value))
    await session.flush()
    return action


async def set_status(
    session: AsyncSession,
    action_id: uuid.UUID,
    status: ActionStatus,
    *,
    failure_reason: str | None = None,
    total_ms: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Move an action to `status` and add the audit event named after it (D-037).

    Run it inside one transaction, so the status and its event are saved together or not at all.
    """
    action = await session.get(Action, action_id, with_for_update=True)
    if action is None:
        raise LookupError("action not found")
    action.status = status.value
    if failure_reason is not None:
        action.failure_reason = failure_reason
    if total_ms is not None:
        action.total_ms = total_ms
    session.add(
        AuditEvent(action_id=action_id, event_type=AuditEventType(status.value), data=data or {})
    )
    await session.flush()
