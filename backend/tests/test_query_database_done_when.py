"""DW-B17, automated: "clean up inactive users" is held; a reviewer's rejection reaches the agent's
waiting poll within seconds; the rewritten query carries `retry_of` and the dashboard API shows the
chain (the manual run in Claude Code is in PROGRESS.md)."""

import asyncio
import time
import uuid

import pytest

from app.services import agent_messages
from tests.action_helpers import token_for
from tests.impact_helpers import replit_diff
from tests.mcp_write_helpers import mcp_for
from tests.workflow_helpers import FAST_LANE_SQL, REPLIT_SQL, WorkflowEnv, rig, runner_for

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

REASON = "Exclude paying customers first."


async def test_reject_in_the_dashboard_then_the_agent_retries(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        held = await api.query(REPLIT_SQL, "Clean up inactive users")
        action_id = held["action_id"]
        assert held["status"] == "awaiting_review"
        assert held["message"] == agent_messages.pending(action_id)

        poll = asyncio.create_task(api.status(action_id, wait_seconds=30))
        await asyncio.sleep(1)
        rejected_at = time.monotonic()
        response = await api.client.post(
            f"/v1/actions/{action_id}/reject",
            headers=token_for(wf_env.user),
            json={"reason": REASON},
        )
        assert response.status_code == 200, response.text
        polled = await asyncio.wait_for(poll, timeout=10)
        assert time.monotonic() - rejected_at < 3
        assert polled["status"] == "rejected"
        assert REASON.rstrip(".") in polled["message"]
        assert polled["message"].endswith(f"pass retry_of={action_id}.")

        retry = await api.query(FAST_LANE_SQL, "Log the cleanup request", retry_of=action_id)
        retry_id = retry["action_id"]
        assert retry["status"] == "committed"

        headers = token_for(wf_env.user)
        original = (await api.client.get(f"/v1/actions/{action_id}", headers=headers)).json()
        rewritten = (await api.client.get(f"/v1/actions/{retry_id}", headers=headers)).json()
    assert original["retried_by"] == retry_id and original["retry_of"] is None
    assert rewritten["retry_of"] == action_id
    assert uuid.UUID(rewritten["id"]) == uuid.UUID(retry_id)
