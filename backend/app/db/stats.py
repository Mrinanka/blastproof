"""Aggregates over the actions a user may see. Every query starts from `visible_actions`."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, Subquery, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.actions import visible_actions
from app.db.enums import Decision


@dataclass(frozen=True)
class Counts:
    by_status: dict[str, int]
    by_decision: dict[str, int]


@dataclass(frozen=True)
class DayTotals:
    counts: Counts
    reviewed: int
    blocked: int
    avg_preview_ms: float | None
    llm_cost_usd: Decimal


async def _counts(
    session: AsyncSession, visible: Subquery, where: list[ColumnElement[bool]]
) -> Counts:
    by_status = (
        await session.execute(
            select(visible.c.status, func.count()).where(*where).group_by(visible.c.status)
        )
    ).all()
    by_decision = (
        await session.execute(
            select(visible.c.decision, func.count())
            .where(*where, visible.c.decision.is_not(None))
            .group_by(visible.c.decision)
        )
    ).all()
    return Counts(dict(by_status), dict(by_decision))


async def all_time(session: AsyncSession, user_id: uuid.UUID, *, is_admin: bool) -> Counts:
    visible = visible_actions(user_id, is_admin=is_admin).subquery()
    return await _counts(session, visible, [])


async def between(
    session: AsyncSession, user_id: uuid.UUID, *, is_admin: bool, start: datetime, end: datetime
) -> DayTotals:
    """Totals for actions created in [start, end), plus the decisions made in that window."""
    visible = visible_actions(user_id, is_admin=is_admin).subquery()
    created = [visible.c.created_at >= start, visible.c.created_at < end]
    row = (
        await session.execute(
            select(
                func.count(case((visible.c.decision == Decision.BLOCK.value, 1))),
                func.avg(visible.c.preview_ms),
                func.coalesce(func.sum(visible.c.llm_cost_usd), 0),
            ).where(*created)
        )
    ).one()
    reviewed = (
        await session.execute(
            select(func.count()).where(
                visible.c.decided_by.is_not(None),
                visible.c.decided_at >= start,
                visible.c.decided_at < end,
            )
        )
    ).scalar_one()
    return DayTotals(
        counts=await _counts(session, visible, created),
        reviewed=reviewed,
        blocked=row[0],
        avg_preview_ms=float(row[1]) if row[1] is not None else None,
        llm_cost_usd=Decimal(row[2]),
    )
