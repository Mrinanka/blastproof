"""Queries on `actions` and their audit events.

Every query that reads actions goes through `visible_actions` (CLAUDE.md, ARCH-13): it filters by
the owner's `user_id` unless admin widening is on. Agents always call it with `is_admin=False`
(D-055), so an admin's agent sees only its owner's actions.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, any_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.enums import ALLOWED_TRANSITIONS, ActionStatus, AuditEventType, Decision
from app.db.models import Action, Agent, AuditEvent, User
from app.services.agent_outcomes import AgentView


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
    pinned_sql: str | None = None,
    sql_display: str | None = None,
    pinned_values: dict[str, str] | None = None,
    retry_of: uuid.UUID | None = None,
    received_data: dict[str, Any] | None = None,
) -> Action:
    """A new action in `received`, with its `received` audit event.

    `received_data` goes on that event; `query_database` marks it `{"kind": "write"}` or `"read"`,
    which the write rate limit counts (`recent_writes`).
    """
    action = Action(
        user_id=user_id,
        agent_id=agent_id,
        system_id=system_id,
        tool=tool,
        intent=intent,
        sql=sql,
        pinned_sql=pinned_sql,
        sql_display=sql_display,
        pinned_values=pinned_values,
        statement_type=statement_type,
        tables=tables or [],
        retry_of=retry_of,
        status=ActionStatus.RECEIVED.value,
    )
    session.add(action)
    await session.flush()
    session.add(
        AuditEvent(
            action_id=action.id,
            event_type=AuditEventType.RECEIVED.value,
            data=received_data or {},
        )
    )
    await session.flush()
    return action


# Distinct from the runner's and the sweep's advisory-lock keys; the second key is the agent.
WRITE_LIMIT_LOCK_KEY = 0x424C5752 + 1
RATE_WINDOW_SECONDS = 60


async def lock_agent_writes(session: AsyncSession, agent_id: uuid.UUID) -> None:
    """Hold this agent's write-limit lock until the transaction ends, so counting and creating an
    action are one step: of 30 calls at once, the count each sees includes the earlier ones."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key, hashtext(:agent))"),
        {"key": WRITE_LIMIT_LOCK_KEY, "agent": str(agent_id)},
    )


async def recent_writes(session: AsyncSession, agent_id: uuid.UUID) -> tuple[int, float | None]:
    """How many writes this agent made in the last 60 s, and how old the oldest is (seconds).

    A write is an action whose `received` event says `kind: write`, not one whose statement_type is
    not `select`: a SELECT that calls an unknown function is a write too. Both sides use the
    database's clock, so no app clock is involved.
    """
    row = (
        await session.execute(
            select(
                func.count(),
                func.extract("epoch", func.clock_timestamp() - func.min(Action.created_at)),
            )
            .select_from(Action)
            .join(
                AuditEvent,
                (AuditEvent.action_id == Action.id)
                & (AuditEvent.event_type == AuditEventType.RECEIVED.value),
            )
            .where(
                Action.agent_id == agent_id,
                AuditEvent.data["kind"].astext == "write",
                Action.created_at > func.clock_timestamp() - timedelta(seconds=RATE_WINDOW_SECONDS),
            )
        )
    ).one()
    age = row[1]
    return row[0], float(age) if age is not None else None


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
class StatusView:
    """What the agent (and a 409) needs to know about an action: no raw SQL, no intent."""

    id: uuid.UUID
    status: str
    decision: str | None
    policy_reason: str | None
    reviewer_note: str | None
    failure_reason: str | None
    decided_by_name: str | None
    created_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None


async def status_view(
    session: AsyncSession, action_id: uuid.UUID, user_id: uuid.UUID, *, is_admin: bool
) -> StatusView | None:
    """The status view of an action this user may see; None for an unknown or someone else's."""
    decider = aliased(User)
    query = (
        visible_actions(user_id, is_admin=is_admin)
        .with_only_columns(
            Action.id,
            Action.status,
            Action.decision,
            Action.policy_reason,
            Action.reviewer_note,
            Action.failure_reason,
            decider.display_name.label("decided_by_name"),
            Action.created_at,
            Action.expires_at,
            Action.decided_at,
        )
        .outerjoin(decider, Action.decided_by == decider.id)
        .where(Action.id == action_id)
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(query)).first()
    return StatusView(**row._asdict()) if row is not None else None


async def agent_view(
    session: AsyncSession, action_id: uuid.UUID, owner_id: uuid.UUID
) -> AgentView | None:
    """What the agent is told about its own action; None for an unknown or someone else's.

    Always through `visible_actions` with admin widening off, so an admin's agent sees only its
    owner's actions (D-055).
    """
    decider = aliased(User)
    row = (
        await session.execute(
            visible_actions(owner_id, is_admin=False)
            .with_only_columns(
                Action.id,
                Action.status,
                Action.decision,
                Action.policy_reason,
                Action.reviewer_note,
                decider.display_name.label("decided_by_name"),
                Action.failure_reason,
                Action.created_at,
                Action.statement_type,
                Action.tables,
                Action.impact,
            )
            .outerjoin(decider, Action.decided_by == decider.id)
            .where(Action.id == action_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return None
    events = (
        await session.execute(
            select(AuditEvent.event_type, AuditEvent.data)
            .where(
                AuditEvent.action_id == action_id,
                AuditEvent.event_type.in_(
                    [
                        AuditEventType.DRIFTED.value,
                        AuditEventType.DECIDED.value,
                        AuditEventType.COMMITTED.value,
                    ]
                ),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    ).all()
    decided = [data for kind, data in events if kind == AuditEventType.DECIDED.value]
    committed = [data for kind, data in events if kind == AuditEventType.COMMITTED.value]
    rows = committed[-1].get("rows") if committed else None
    return AgentView(
        id=row.id,
        status=row.status,
        decision=row.decision,
        policy_reason=row.policy_reason,
        reviewer_note=row.reviewer_note,
        decided_by_name=row.decided_by_name,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        statement_type=row.statement_type,
        tables=list(row.tables or []),
        drifts=sum(1 for kind, _ in events if kind == AuditEventType.DRIFTED.value),
        ai_unavailable=bool(decided and decided[-1].get("ai_unavailable")),
        committed_rows=rows if isinstance(rows, int) else None,
        impact=row.impact,
    )


async def claim_decision(
    session: AsyncSession,
    action_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    is_admin: bool,
    approved: bool,
    note: str | None,
    now: datetime,
) -> bool:
    """Claim a review for `user_id` with one conditional UPDATE; True if this call won it.

    The condition is the same as `can_decide`: awaiting review and not past `expires_at`. Of two
    callers at once, the second waits for the first's row lock, sees the new status and claims
    nothing (D-177). Visibility is part of the statement, so no caller can claim what it cannot see.
    """
    visible = visible_actions(user_id, is_admin=is_admin).with_only_columns(Action.id)
    status = ActionStatus.APPROVED if approved else ActionStatus.REJECTED
    claimed = await session.execute(
        update(Action)
        .where(
            Action.id == action_id,
            Action.id.in_(visible),
            Action.status == ActionStatus.AWAITING_REVIEW.value,
            or_(Action.expires_at.is_(None), Action.expires_at > now),
        )
        .values(status=status.value, decided_by=user_id, decided_at=now, reviewer_note=note)
        .returning(Action.id)
    )
    return claimed.scalar_one_or_none() is not None


async def expire_if_overdue(session: AsyncSession, action_id: uuid.UUID, now: datetime) -> bool:
    """Expire a review whose deadline has passed. `decided_by` stays null (BE-16).

    Locks the row first, so a decision that won the race is seen, and nothing is expired twice.
    """
    action = (
        await session.execute(
            select(Action)
            .where(Action.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        action is None
        or action.status != ActionStatus.AWAITING_REVIEW.value
        or action.expires_at is None
        or action.expires_at > now
    ):
        return False
    await set_status(
        session,
        action_id,
        ActionStatus.EXPIRED,
        decided_at=now,
        data={"reason": "review_timeout"},
    )
    return True


async def overdue_reviews(session: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """Reviews waiting past their `expires_at`."""
    return list(
        (
            await session.execute(
                select(Action.id).where(
                    Action.status == ActionStatus.AWAITING_REVIEW.value, Action.expires_at <= now
                )
            )
        )
        .scalars()
        .all()
    )


async def stuck_actions(
    session: AsyncSession, statuses: frozenset[ActionStatus], cutoff: datetime
) -> list[uuid.UUID]:
    """Actions in one of `statuses` since before `cutoff`.

    Timed from the latest audit event named after the action's current status, not from
    `created_at`: a re-preview after drift (step 16) re-enters `previewing` hours later.
    """
    entered = (
        select(func.max(AuditEvent.created_at))
        .where(AuditEvent.action_id == Action.id, AuditEvent.event_type == Action.status)
        .correlate(Action)
        .scalar_subquery()
    )
    query = select(Action.id).where(
        Action.status.in_([status.value for status in statuses]), entered < cutoff
    )
    return list((await session.execute(query)).scalars().all())


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
