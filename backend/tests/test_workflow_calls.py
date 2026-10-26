"""How the graph is called (D-176, D-177): `durability="sync"`, `version="v2"`, and a resume only
for a thread that waits at `human_review`. A fake graph records the call (no database)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.types import Command

from app.workflow.graph import NotAwaitingReviewError, resume_write, run_config, run_write

ACTION = uuid.UUID("00000000-0000-4000-8000-000000000014")
AGENT = uuid.UUID("00000000-0000-4000-8000-0000000000a4")


class FakeGraph:
    def __init__(self, next_nodes: tuple[str, ...] = ("human_review",)) -> None:
        self.next_nodes = next_nodes
        self.calls: list[tuple[Any, Any, dict[str, Any]]] = []

    async def ainvoke(self, payload: Any, config: Any, **kwargs: Any) -> str:
        self.calls.append((payload, config, kwargs))
        return "done"

    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(next=self.next_nodes)


def test_the_run_config_names_the_thread_and_the_trace() -> None:
    config = run_config(ACTION, AGENT, "production")
    assert config["configurable"] == {"thread_id": str(ACTION)}
    assert config["run_name"] == "write_pipeline"
    assert config["metadata"] == {
        "action_id": str(ACTION),
        "agent_id": str(AGENT),
        "env": "production",
    }


async def test_a_run_is_durable_and_v2() -> None:
    graph = FakeGraph()
    await run_write(graph, action_id=ACTION, agent_id=AGENT, env="test")  # type: ignore[arg-type]
    [(payload, config, kwargs)] = graph.calls
    assert payload == {"action_id": str(ACTION)}
    assert config["configurable"]["thread_id"] == str(ACTION)
    assert kwargs == {"durability": "sync", "version": "v2"}


@pytest.mark.parametrize("approved", [True, False])
async def test_a_resume_sends_the_decision_to_a_paused_thread(approved: bool) -> None:
    graph = FakeGraph()
    await resume_write(
        graph,  # type: ignore[arg-type]
        action_id=ACTION,
        agent_id=AGENT,
        env="test",
        approved=approved,
    )
    [(payload, _config, kwargs)] = graph.calls
    assert isinstance(payload, Command)
    assert payload.resume == {"approved": approved}
    assert kwargs == {"durability": "sync", "version": "v2"}


@pytest.mark.parametrize("next_nodes", [(), ("commit",), ("shadow",), ("human_review", "x")])
async def test_a_resume_of_any_other_thread_state_is_refused(next_nodes: tuple[str, ...]) -> None:
    graph = FakeGraph(next_nodes)
    with pytest.raises(NotAwaitingReviewError):
        await resume_write(
            graph,  # type: ignore[arg-type]
            action_id=ACTION,
            agent_id=AGENT,
            env="test",
            approved=True,
        )
    assert graph.calls == []
