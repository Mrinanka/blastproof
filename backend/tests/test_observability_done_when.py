"""OBS-01, OBS-02, SEC-14: the observability half of the security pass, through the real graph.

Per path: every node that ran logs `stage_done` with an integer `duration_ms` and the action's
`action_id`; a failure logs `fail_closed` with its duration; no log line holds a planted value.
Logs are read from the real JSON handler, so the redactor and the context binding are in the loop.
"""

import io
import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.base import BaseTracer
from langgraph.types import Command

import app.workflow.graph as graph_module
from app.config import Settings
from app.logging import configure_logging
from app.services.redaction import set_known_secrets
from app.workflow.graph import run_config
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL, FakeLlm, replit_diff, report
from tests.workflow_helpers import (
    CALM_HEADLINE,
    DROP_SQL,
    FAST_LANE_SQL,
    SMALL_INSERT_SQL,
    FakeShadowRunner,
    WorkflowEnv,
    approve,
    claim,
    new_action,
    rig,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


@pytest.fixture
def logbuf() -> io.StringIO:
    # Forget a logger bound under an earlier configuration (see test_workflow_fail_closed).
    graph_module.logger.__dict__.pop("bind", None)
    buf = io.StringIO()
    configure_logging(stream=buf)
    yield buf
    set_known_secrets([])


def lines(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def stages(buf: io.StringIO, action_id: uuid.UUID) -> list[str]:
    done = [x for x in lines(buf) if x["event"] == "stage_done"]
    for line in done:
        assert isinstance(line["duration_ms"], int) and line["duration_ms"] >= 0
        assert line["action_id"] == str(action_id)
    return [x["stage"] for x in done]


async def test_fast_lane_logs_each_stage(wf_env: WorkflowEnv, logbuf: io.StringIO) -> None:
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, FAST_LANE_SQL)
        await r.run(action_id)
    assert stages(logbuf, action_id) == ["classify", "pre_rules", "fast_lane", "approve", "commit"]


async def test_allow_path_logs_the_full_pipeline(wf_env: WorkflowEnv, logbuf: io.StringIO) -> None:
    from tests.test_workflow_done_when import PRODUCTS_INSERT

    async with rig(wf_env, diff=PRODUCTS_INSERT) as r:
        action_id = new_action(wf_env, SMALL_INSERT_SQL)
        await r.run(action_id)
    assert stages(logbuf, action_id) == [
        "classify",
        "pre_rules",
        "shadow",
        "post_rules",
        "retrieve",
        "impact",
        "judge",
        "decide",
        "approve",
        "commit",
    ]


async def test_review_path_then_approve_logs_commit(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
        assert stages(logbuf, action_id) == [
            "classify",
            "pre_rules",
            "shadow",
            "post_rules",
            "retrieve",
            "impact",
            "judge",
            "decide",
            "request_review",
        ]
        await approve(r, action_id)
    assert stages(logbuf, action_id)[-1] == "commit"


async def test_pre_rule_block_logs_the_block_stage(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    async with rig(wf_env) as r:
        action_id = new_action(wf_env, DROP_SQL)
        await r.run(action_id)
    assert stages(logbuf, action_id) == ["classify", "pre_rules", "block"]


async def test_fail_closed_line_has_a_duration_and_the_action(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    class Failing(FakeShadowRunner):
        async def run(self, **_: Any) -> Any:
            raise RuntimeError("boom")

    async with rig(wf_env, shadow=Failing(replit_diff())) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
    [failed] = [x for x in lines(logbuf) if x["event"] == "fail_closed"]
    assert isinstance(failed["duration_ms"], int)
    assert failed["action_id"] == str(action_id)
    assert failed["node"] == "shadow"
    assert "shadow" not in stages(logbuf, action_id)


async def test_a_hanging_judge_still_logs_its_stage(
    wf_env: WorkflowEnv, logbuf: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setattr(graph_module, "JUDGE_TIMEOUT_MARGIN_SECONDS", 0.05)
    async with rig(wf_env, diff=replit_diff(), settings={"llm_timeout_seconds": 0}) as r:

        async def hang(*_: Any, **__: Any) -> Any:
            await asyncio.sleep(3600)

        monkeypatch.setattr(r.judge, "assess", hang)
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        await r.run(action_id)
    assert "judge" in stages(logbuf, action_id)
    assert not [x for x in lines(logbuf) if x["event"] == "fail_closed"]


def test_structlog_is_the_configured_one(logbuf: io.StringIO) -> None:
    structlog.get_logger().info("probe")
    assert lines(logbuf)[0]["event"] == "probe"


# --- the decision routes ------------------------------------------------------------------------


async def test_approve_route_lines_carry_the_action_id(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    from tests.test_decisions_api import api_for
    from tests.workflow_helpers import paused_action, paused_rig, runner_for

    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        runner = runner_for(r)
        async with api_for(wf_env, runner) as api:
            configure_logging(stream=logbuf)  # create_app pointed logging at stdout
            logbuf.truncate(0)
            logbuf.seek(0)
            response = await api.approve(wf_env.user, action_id)
            assert response.status_code == 200, response.text
            await runner.drain()
    wanted = {"decision_requested", "decision_claimed", "request_finished"}
    seen = {x["event"]: x for x in lines(logbuf) if x["event"] in wanted}
    assert set(seen) == wanted
    for line in seen.values():
        assert line["action_id"] == str(action_id)
        assert line["request_id"]


async def test_reject_route_lines_carry_the_action_id(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    from tests.test_decisions_api import api_for
    from tests.workflow_helpers import paused_action, paused_rig, runner_for

    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        runner = runner_for(r)
        async with api_for(wf_env, runner) as api:
            configure_logging(stream=logbuf)  # create_app pointed logging at stdout
            logbuf.truncate(0)
            logbuf.seek(0)
            response = await api.reject(wf_env.user, action_id)
            assert response.status_code == 200, response.text
            await runner.drain()
    finished = [x for x in lines(logbuf) if x["event"] == "request_finished"]
    assert finished and finished[-1]["action_id"] == str(action_id)


async def test_another_users_decision_is_a_404_and_the_log_holds_none_of_their_data(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    from tests.test_decisions_api import api_for
    from tests.workflow_helpers import paused_action, paused_rig, runner_for

    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        async with api_for(wf_env, runner_for(r)) as api:
            configure_logging(stream=logbuf)
            response = await api.approve(api.other, action_id)
            assert response.status_code == 404
            raw = logbuf.getvalue()
    assert wf_env.user.email not in raw
    assert REPLIT_INTENT not in raw


# --- a write over MCP, end to end ---------------------------------------------------------------


async def test_mcp_write_logs_no_planted_value_and_binds_both_ids(
    wf_env: WorkflowEnv, logbuf: io.StringIO
) -> None:
    from tests.mcp_write_helpers import mcp_for
    from tests.workflow_helpers import runner_for

    key = "bp_" + "Zy9Xw8Vu7T" * 4 + "abc"
    intent = (
        "Clean up for ana@example.com, call +1 (415) 555-0132, "
        f"via postgresql://u:secretpass1@h/db with {key}"
    )
    sql = "DELETE FROM users WHERE email = 'bob@example.com'"
    async with rig(wf_env, diff=replit_diff()) as r, mcp_for(wf_env, runner_for(r)) as api:
        configure_logging(stream=logbuf)  # create_app pointed logging at stdout
        logbuf.truncate(0)
        logbuf.seek(0)
        result = await api.query(sql, intent)
        assert result["status"] == "awaiting_review"
        action_id = result["action_id"]
        status = await api.status(action_id)
        assert status["status"] == "awaiting_review"
    raw = logbuf.getvalue()
    for planted in ("ana@example.com", "bob@example.com", "555-0132", "secretpass1", key):
        assert planted not in raw

    entries = lines(logbuf)
    start = next(i for i, x in enumerate(entries) if x["event"] == "action_received")
    finished = next(
        i for i, x in enumerate(entries) if x["event"] == "request_finished" and i > start
    )
    # `request_finished` is the middleware's own line: the MCP tool runs in a task of its own, so
    # it does not see the ids bound inside the tool.
    assert finished - start > 3
    for line in entries[start:finished]:
        assert line.get("request_id"), line["event"]
        assert line.get("action_id") == action_id, line["event"]


async def test_action_status_binds_the_action_only_for_its_owner(wf_env: WorkflowEnv) -> None:
    from app.core.auth import CurrentAgent
    from app.core.clock import SystemClock
    from app.db.session import make_engine, make_sessionmaker
    from app.services.agent_calls import CallContext, action_status
    from tests.conftest import asyncpg_url

    action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
    engine = make_engine(asyncpg_url(wf_env.db.app_url))
    try:
        sessionmaker = make_sessionmaker(engine)
        owner = CurrentAgent(wf_env.agent_id, "agent", wf_env.user.id)
        stranger = CurrentAgent(uuid.uuid4(), "agent", uuid.uuid4())
        for agent, bound in ((owner, True), (stranger, False)):
            ctx = CallContext(
                Settings(_env_file=None, app_env="test"),
                sessionmaker,
                agent,
                SystemClock(),
                lambda: None,
            )
            structlog.contextvars.clear_contextvars()
            await action_status(ctx, str(action_id))
            assert ("action_id" in structlog.contextvars.get_contextvars()) is bound
    finally:
        structlog.contextvars.clear_contextvars()
        await engine.dispose()


# --- OBS-01: every write is traced and tagged ---------------------------------------------------


@dataclass
class TracedLlm(FakeLlm):
    """A fake structured model that is a real LangChain runnable, so LangChain traces the call
    under the `run_name` and metadata the engine passes (the plain fakes are never traced)."""

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        async def call(inner: Any) -> Any:
            return await FakeLlm.ainvoke(self, inner)

        return await RunnableLambda(call).ainvoke(messages, config)


class RecordingTracer(BaseTracer):
    """Collects each finished root run with its children. Passed in the run config it is
    inheritable, unlike the collector of `collect_runs`, so nodes and models nest under the root."""

    def __init__(self) -> None:
        super().__init__()
        self.roots: list[Any] = []

    def _persist_run(self, run: Any) -> None:
        self.roots.append(run)


def all_runs(runs: list[Any]) -> list[Any]:
    found: list[Any] = []
    for run in runs:
        found.append(run)
        found += all_runs(run.child_runs)
    return found


def tags_of(run: Any) -> dict[str, Any]:
    return (run.extra or {}).get("metadata", {})


async def test_a_write_is_traced_with_its_ids_and_the_models_nest_under_it(
    wf_env: WorkflowEnv,
) -> None:
    tracer = RecordingTracer()
    summary = TracedLlm(parsed=report(CALM_HEADLINE))
    async with rig(wf_env, diff=replit_diff(), summary_llm=summary) as r:
        action_id = new_action(wf_env, REPLIT_SQL, intent=REPLIT_INTENT)
        config = {**run_config(action_id, wf_env.agent_id, "test"), "callbacks": [tracer]}
        await r.graph.ainvoke(
            {"action_id": str(action_id)}, config, durability="sync", version="v2"
        )
        assert claim(wf_env, action_id, approved=True) == 1
        await r.graph.ainvoke(
            Command(resume={"approved": True}), config, durability="sync", version="v2"
        )
    assert [run.name for run in tracer.roots] == ["write_pipeline", "write_pipeline"]
    for root in tracer.roots:
        meta = tags_of(root)
        assert meta["action_id"] == str(action_id)
        assert meta["agent_id"] == str(wf_env.agent_id)
        assert meta["env"] == "test"
    first = all_runs([tracer.roots[0]])
    [summary_run] = [run for run in first if run.name == "impact_summary"]
    assert tags_of(summary_run)["action_id"] == str(action_id)
    assert tags_of(summary_run)["env"] == "test"
    assert {"classify", "shadow", "judge", "decide"} <= {run.name for run in first}
