"""Queries on `actions` and their audit events.

Every query that reads actions goes through `visible_actions` (CLAUDE.md, ARCH-13): it filters by
the owner's `user_id` unless admin widening is on. Agents always call it with `is_admin=False`
(D-055), so an admin's agent sees only its owner's actions.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, any_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.enums import ALLOWED_TRANSITIONS, ActionStatus, AuditEventType, Decision
from app.db.models import Action, Agent, AuditEvent, User


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


class IllegalTransitionError(RuntimeError):
    """A status change the state machine does not allow (BE-16). Callers fail closed."""


async def set_status(
    session: AsyncSession,
    action_id: uuid.UUID,
    status: ActionStatus,
    *,
    failure_reason: str | None = None,
    total_ms: int | None = None,
    data: dict[str, Any] | None = None,
    decision: Decision | None = None,
    policy_reason: str | None = None,
    decided_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Move an action to `status` and add the audit event named after it (D-037).

    Run it inside one transaction, so the status and its event are saved together or not at all.
    Refuses a move the state machine does not allow (`IllegalTransitionError`, BE-16).
    """
    action = await session.get(Action, action_id, with_for_update=True)
    if action is None:
        raise LookupError("action not found")
    if status not in ALLOWED_TRANSITIONS[ActionStatus(action.status)]:
        raise IllegalTransitionError(f"{action.status} -> {status.value}")
    action.status = status.value
    if failure_reason is not None:
        action.failure_reason = failure_reason
    if total_ms is not None:
        action.total_ms = total_ms
    if decision is not None:
        action.decision = decision.value
    if policy_reason is not None:
        action.policy_reason = policy_reason
    if decided_at is not None:
        action.decided_at = decided_at
    if expires_at is not None:
        action.expires_at = expires_at
    session.add(
        AuditEvent(
            action_id=action_id,
            event_type=AuditEventType(status.value),
            data=data or {},
            created_at=func.clock_timestamp(),
        )
    )
    await session.flush()


async def add_event(
    session: AsyncSession,
    action_id: uuid.UUID,
    event_type: AuditEventType,
    data: dict[str, Any] | None = None,
) -> None:
    """An audit event that is not a status change (`shadow_done`, `decided`, `fail_closed`).

    `clock_timestamp()`, not the column default `now()`: `now()` is the start of the transaction, so
    two events written together would tie and the timeline (ordered by time, then a random id)
    could show them in the wrong order.
    """
    session.add(
        AuditEvent(
            action_id=action_id,
            event_type=event_type.value,
            data=data or {},
            created_at=func.clock_timestamp(),
        )
    )
    await session.flush()


@dataclass(frozen=True)
class ListFilters:
    statuses: tuple[ActionStatus, ...] = ()
    agent_id: uuid.UUID | None = None
    table: str | None = None
    created_from: datetime | None = None  # inclusive
    created_to: datetime | None = None  # exclusive
    newest_first: bool = True
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True)
class SummaryRow:
    id: uuid.UUID
    status: str
    decision: str | None
    risk_level: str | None
    intent: str | None
    headline: str | None
    tables: list[str]
    created_at: datetime
    agent_id: uuid.UUID
    agent_name: str
    decider_id: uuid.UUID | None
    decider_name: str | None


def _conditions(filters: ListFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.statuses:
        conditions.append(Action.status.in_([status.value for status in filters.statuses]))
    if filters.agent_id is not None:
        conditions.append(Action.agent_id == filters.agent_id)
    if filters.table is not None:
        conditions.append(filters.table == any_(Action.tables))
    if filters.created_from is not None:
        conditions.append(Action.created_at >= filters.created_from)
    if filters.created_to is not None:
        conditions.append(Action.created_at < filters.created_to)
    return conditions


async def list_visible_actions(
    session: AsyncSession, user_id: uuid.UUID, *, is_admin: bool, filters: ListFilters
) -> tuple[list[SummaryRow], int]:
    """One page of the actions this user may see, and how many match in all.

    Selects only the columns a list row needs; the impact report's headline is pulled out in SQL so
    the large JSONB columns are never loaded. Ordered by `created_at`, then `id`: stable paging.
    """
    conditions = _conditions(filters)
    decider = aliased(User)
    total = (
        await session.execute(
            visible_actions(user_id, is_admin=is_admin)
            .with_only_columns(func.count(), maintain_column_froms=True)
            .where(*conditions)
        )
    ).scalar_one()
    direction = (lambda column: column.desc()) if filters.newest_first else (lambda c: c.asc())
    query = (
        visible_actions(user_id, is_admin=is_admin)
        .with_only_columns(
            Action.id,
            Action.status,
            Action.decision,
            Action.risk_level,
            Action.intent,
            Action.impact_report["headline"].astext.label("headline"),
            Action.tables,
            Action.created_at,
            Agent.id.label("agent_id"),
            Agent.name.label("agent_name"),
            decider.id.label("decider_id"),
            decider.display_name.label("decider_name"),
        )
        .join_from(Action, Agent, Action.agent_id == Agent.id)
        .outerjoin(decider, Action.decided_by == decider.id)
        .where(*conditions)
        .order_by(direction(Action.created_at), direction(Action.id))
        .limit(filters.limit)
        .offset(filters.offset)
    )
    rows = (await session.execute(query)).all()
    return [SummaryRow(**row._asdict()) for row in rows], total


@dataclass(frozen=True)
class DetailRows:
    action: Action
    agent: Agent
    owner: User
    decider: User | None
    events: list[AuditEvent]
    retried_by: uuid.UUID | None


async def get_action_detail_rows(
    session: AsyncSession, action_id: uuid.UUID, user_id: uuid.UUID, *, is_admin: bool
) -> DetailRows | None:
    """An action with what its detail page shows, or None for an unknown or someone else's."""
    action = await get_visible_action(session, action_id, user_id, is_admin=is_admin)
    if action is None:
        return None
    agent = await session.get(Agent, action.agent_id)
    owner = await session.get(User, action.user_id)
    decider = await session.get(User, action.decided_by) if action.decided_by else None
    assert agent is not None and owner is not None  # foreign keys
    events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.action_id == action_id)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        .scalars()
        .all()
    )
    retried_by = (
        await session.execute(
            visible_actions(user_id, is_admin=is_admin)
            .with_only_columns(Action.id)
            .where(Action.retry_of == action_id)
            .order_by(Action.created_at.desc(), Action.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return DetailRows(action, agent, owner, decider, list(events), retried_by)
