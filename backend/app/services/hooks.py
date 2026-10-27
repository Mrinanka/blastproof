"""The hook check: run the rules, store a masked `hook_events` row, answer allow or deny."""

import time

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentAgent
from app.db import hook_events as hook_events_db
from app.schemas.hook import HookDecisionResponse, HookRequest
from app.services import hook_rules

logger = structlog.get_logger()


async def check_hook_call(
    session: AsyncSession, agent: CurrentAgent, request: HookRequest
) -> HookDecisionResponse:
    started = time.monotonic()
    verdict = hook_rules.evaluate(
        request.tool_name, request.tool_input, request.cwd, request.project_dir
    )
    await hook_events_db.insert_hook_event(
        session,
        user_id=agent.owner_id,
        agent_id=agent.agent_id,
        tool=request.tool_name[:64],
        summary=hook_rules.summarize(request.tool_name, request.tool_input),
        decision=verdict.decision,
        reason=verdict.reason,
        rule=verdict.rule,
    )
    await session.commit()
    # Never the command or the path: only what was decided.
    logger.info(
        "hook_checked",
        tool=request.tool_name[:64],
        decision=verdict.decision,
        rule=verdict.rule,
        duration_ms=round((time.monotonic() - started) * 1000, 1),
    )
    return HookDecisionResponse(
        decision=verdict.decision,
        reason=verdict.reason if verdict.decision == "deny" else None,
        rule=verdict.rule,
    )
