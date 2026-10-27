"""The dashboard's numbers: counts and sums from the database, nothing estimated.

"Today" is the UTC day (D-051). Every status and decision is present, with 0 when none.
"""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.clock import Clock
from app.db import stats as stats_db
from app.db.enums import ActionStatus, Decision
from app.schemas.stats import StatsResponse, TodayStats


def day_window(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the UTC day that contains `now`."""
    start = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def fill_status(counts: dict[str, int]) -> dict[ActionStatus, int]:
    return {status: counts.get(status.value, 0) for status in ActionStatus}


def fill_decision(counts: dict[str, int]) -> dict[Decision, int]:
    return {decision: counts.get(decision.value, 0) for decision in Decision}


async def build_stats(session: AsyncSession, user: CurrentUser, clock: Clock) -> StatsResponse:
    now = clock.now()
    start, end = day_window(now)
    everything = await stats_db.all_time(session, user.id, is_admin=user.is_admin)
    today = await stats_db.between(session, user.id, is_admin=user.is_admin, start=start, end=end)
    by_status = fill_status(everything.by_status)
    return StatsResponse(
        pending_reviews=by_status[ActionStatus.AWAITING_REVIEW],
        by_status=by_status,
        by_decision=fill_decision(everything.by_decision),
        today=TodayStats(
            day=start.date(),
            actions=sum(today.counts.by_status.values()),
            by_status=fill_status(today.counts.by_status),
            by_decision=fill_decision(today.counts.by_decision),
            reviewed=today.reviewed,
            blocked=today.blocked,
            avg_preview_ms=today.avg_preview_ms,
            llm_cost_usd=float(today.llm_cost_usd),
        ),
    )
