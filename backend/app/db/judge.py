"""Saving the risk judge's result (backend plan step 13) on its action."""

import uuid

from sqlalchemy import null, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.impact import add_usage
from app.db.models import Action
from app.services.judge import JudgeResult


async def save_judge(session: AsyncSession, action_id: uuid.UUID, outcome: JudgeResult) -> None:
    """Store the judge's result and risk level and add this call's tokens and cost, in the
    caller's transaction.

    Changes no status or decision: step 14 decides and records that. The risk level is the
    judge's own, and NULL when the judge failed or was skipped (none is invented from the rules).
    """
    risk_level = outcome.verdict.risk_level if outcome.verdict is not None else null()
    await session.execute(
        update(Action)
        .where(Action.id == action_id)
        .values(
            judge=outcome.to_json(),
            risk_level=risk_level,
            llm_input_tokens=add_usage(Action.llm_input_tokens, outcome.input_tokens),
            llm_output_tokens=add_usage(Action.llm_output_tokens, outcome.output_tokens),
            llm_cost_usd=add_usage(Action.llm_cost_usd, outcome.cost_usd),
        )
    )
    await session.flush()
