"""Saving an impact summary (backend plan step 12) on its action."""

import uuid
from typing import Any

from sqlalchemy import func, null, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.models import Action
from app.services.impact import ImpactResult


def _add(column: InstrumentedAttribute[Any], amount: object) -> Any:
    """The running total plus this call. An unknown amount makes the total unknown (NULL), which
    the cost cap treats as over budget (step 14)."""
    if amount is None:
        return null()
    return func.coalesce(column, 0) + amount


async def save_impact(session: AsyncSession, action_id: uuid.UUID, result: ImpactResult) -> None:
    """Store the report and add this call's tokens and cost, in the caller's transaction.

    Adds rather than replaces: a re-preview after drift calls the model again, and both calls count
    toward the per-action cost cap.
    """
    await session.execute(
        update(Action)
        .where(Action.id == action_id)
        .values(
            impact_report=result.report_json(),
            llm_input_tokens=_add(Action.llm_input_tokens, result.input_tokens),
            llm_output_tokens=_add(Action.llm_output_tokens, result.output_tokens),
            llm_cost_usd=_add(Action.llm_cost_usd, result.cost_usd),
        )
    )
    await session.flush()
