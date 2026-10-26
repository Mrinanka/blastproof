"""Saving the risk judge's result (backend plan step 13) on its action."""

import uuid
from typing import Any

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
    values: dict[str, Any] = {"judge": outcome.to_json(), "risk_level": risk_level}
    if not _made_no_call(outcome):
        values |= {
            "llm_input_tokens": add_usage(Action.llm_input_tokens, outcome.input_tokens),
            "llm_output_tokens": add_usage(Action.llm_output_tokens, outcome.output_tokens),
            "llm_cost_usd": add_usage(Action.llm_cost_usd, outcome.cost_usd),
        }
    await session.execute(update(Action).where(Action.id == action_id).values(**values))
    await session.flush()


def _made_no_call(outcome: JudgeResult) -> bool:
    """A skipped judge (cost cap, no knowledge) spent nothing, so it adds nothing. Adding its zero
    would turn an unknown total (NULL) into 0, which the cost cap would then read as "free"."""
    return outcome.cost_usd == 0 and not outcome.input_tokens and not outcome.output_tokens
