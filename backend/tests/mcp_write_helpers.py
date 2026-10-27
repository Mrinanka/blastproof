"""Step 17 helpers: the MCP tools called over HTTP, async, with a runner from the workflow rig.

A sync `TestClient` would run the rig's checkpointer pool on another event loop, so these tests use
`httpx.ASGITransport` with the app's own lifespan and install the rig's runner on `app.state`
(the `test_decisions_api.py` pattern).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.main import create_app
from app.workflow.runner import WorkflowRunner
from tests.conftest import MCP_BASE_URL, TestAgent, bearer, mcp_call, mcp_settings, parse_rpc
from tests.workflow_helpers import WorkflowEnv


@dataclass
class McpApi:
    client: httpx.AsyncClient
    env: WorkflowEnv
    agent: TestAgent

    async def call(
        self,
        tool: str,
        arguments: dict[str, object] | None = None,
        *,
        agent: TestAgent | None = None,
    ) -> dict[str, Any]:
        body, headers = mcp_call(tool, arguments)
        key = (agent or self.agent).key
        response = await self.client.post("/mcp", json=body, headers=headers | bearer(key))
        assert response.status_code == 200, response.text[:300]
        message = parse_rpc(response.headers.get("content-type", ""), response.text)
        result = message["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    async def query(
        self,
        sql: str,
        intent: str = "test",
        *,
        retry_of: object = None,
        agent: TestAgent | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, object] = {"sql": sql, "intent": intent}
        if retry_of is not None:
            arguments["retry_of"] = str(retry_of)
        return await self.call("query_database", arguments, agent=agent)

    async def status(
        self, action_id: object, wait_seconds: int = 0, *, agent: TestAgent | None = None
    ) -> dict[str, Any]:
        arguments: dict[str, object] = {"action_id": str(action_id), "wait_seconds": wait_seconds}
        return await self.call("get_action_status", arguments, agent=agent)


@asynccontextmanager
async def mcp_for(
    env: WorkflowEnv, runner: WorkflowRunner | None, **settings: object
) -> AsyncIterator[McpApi]:
    app = create_app(mcp_settings(env.db, **settings))
    async with app.router.lifespan_context(app):
        app.state.runner = runner
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=MCP_BASE_URL) as client:
            yield McpApi(client, env, env.db.make_agent(env.user.id))


def action_ids(env: WorkflowEnv, agent: TestAgent | None = None) -> list[uuid.UUID]:
    """Every action recorded, or only those of one agent."""
    if agent is None:
        return [row[0] for row in env.db.execute("SELECT id FROM actions ORDER BY created_at")]
    return [
        row[0]
        for row in env.db.execute(
            "SELECT id FROM actions WHERE agent_id = %s ORDER BY created_at", (agent.id,)
        )
    ]
