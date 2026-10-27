import datetime as dt

from pydantic import BaseModel

from app.db.enums import ActionStatus, Decision


class TodayStats(BaseModel):
    """The UTC day so far. `reviewed`: decisions made today; `blocked`: actions created today."""

    day: dt.date
    actions: int
    by_status: dict[ActionStatus, int]
    by_decision: dict[Decision, int]
    reviewed: int
    blocked: int
    avg_preview_ms: float | None  # None when no action was previewed today
    llm_cost_usd: float  # only the costs that are known


class StatsResponse(BaseModel):
    pending_reviews: int  # awaiting_review right now
    by_status: dict[ActionStatus, int]  # all time; every status is present
    by_decision: dict[Decision, int]  # all time; every decision is present
    today: TodayStats
