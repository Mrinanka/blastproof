"""LangSmith experiments (backend plan §14, EVAL-09): a fake client and a fake `aevaluate`."""

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.config import DEFAULT_POLICY_FILE, Settings
from app.services.policy import load_policy
from evals import langsmith_experiments as ls
from evals.metrics import Outcome
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import SCENARIOS_DIR

LABELED = SCENARIOS_DIR / "labeled.yaml"
POLICY = load_policy(DEFAULT_POLICY_FILE)
SETTINGS = Settings(
    _env_file=None,
    app_env="test",
    summary_model="google_genai:summary-x",
    judge_model="google_genai:judge-y",
    llm_prices={
        "google_genai:summary-x": {"input_per_million": 1, "output_per_million": 2},
        "google_genai:judge-y": {"input_per_million": 1, "output_per_million": 2},
    },
)


def labeled() -> list[SqlScenario]:
    return [s for s in load_scenario_file(LABELED).scenarios if isinstance(s, SqlScenario)]


class FakeClient:
    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.created: list[str] = []
        self.examples: list[dict[str, Any]] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return self.existing

    def create_dataset(self, *, dataset_name: str, description: str | None = None) -> None:
        self.created.append(dataset_name)

    def create_examples(self, *, dataset_name: str, examples: list[dict[str, Any]]) -> None:
        self.examples.extend(examples)


class FakePipeline:
    def __init__(self, decision: str | None = "review", raises: bool = False) -> None:
        self.decision = decision
        self.raises = raises
        self.ran: list[str] = []

    async def run(self, scenario: SqlScenario, file: str, timeout_seconds: float) -> Outcome:
        self.ran.append(scenario.id)
        if self.raises:
            raise RuntimeError("secret-sql-text")
        return Outcome(
            scenario.id, file, "sql", scenario.expected_decision, self.decision, reason="r"
        )


class FakeAevaluate:
    """Records each call, and runs the target and evaluators over a few examples like LangSmith."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on
        self.outputs: list[dict[str, Any]] = []

    async def __call__(self, target: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["experiment_prefix"] == self.fail_on:
            raise RuntimeError("langsmith down")
        for scenario in labeled()[:3]:
            inputs = {"id": scenario.id, "sql": scenario.sql, "intent": scenario.intent}
            outputs = await target(inputs)
            self.outputs.append(outputs)
            reference = {"expected_decision": scenario.expected_decision}
            for evaluator in kwargs["evaluators"]:
                params = evaluator.__code__.co_varnames[: evaluator.__code__.co_argcount]
                given = {"outputs": outputs, "reference_outputs": reference}
                assert evaluator(**{k: v for k, v in given.items() if k in params})["key"]

        class Result:
            experiment_name = f"{kwargs['experiment_prefix']}-abc123"

        return Result()


def factory(pipeline: FakePipeline, opened: list[str] | None = None) -> Any:
    @asynccontextmanager
    async def open_pipeline(variant: str) -> AsyncIterator[FakePipeline]:
        if opened is not None:
            opened.append(variant)
        yield pipeline

    return open_pipeline


async def run_all(aevaluate: FakeAevaluate, pipeline: FakePipeline, opened: Any = None) -> Any:
    return await ls.run_experiments(
        FakeClient(),
        aevaluate,
        settings=SETTINGS,
        policy=POLICY,
        labeled_path=LABELED,
        timeout_seconds=5,
        pipeline=factory(pipeline, opened),
    )


def test_the_dataset_name_changes_when_the_file_changes(tmp_path: Path) -> None:
    copy = tmp_path / "labeled.yaml"
    shutil.copy(LABELED, copy)
    first = ls.dataset_name(copy)
    assert first == ls.dataset_name(copy)
    assert first.startswith("blastproof-labeled-")
    assert len(first.removeprefix("blastproof-labeled-")) == 8
    copy.write_text(copy.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert ls.dataset_name(copy) != first


def test_a_missing_dataset_is_created_with_every_example() -> None:
    client = FakeClient()
    name = ls.ensure_dataset(client, LABELED, labeled())
    assert client.created == [name]
    assert len(client.examples) == len(labeled())


def test_an_existing_dataset_is_not_uploaded_again() -> None:
    client = FakeClient(existing=True)
    ls.ensure_dataset(client, LABELED, labeled())
    assert client.created == []
    assert client.examples == []


def test_example_inputs_and_outputs_have_the_right_shape() -> None:
    rows = ls.example_rows(labeled())
    for row, scenario in zip(rows, labeled(), strict=True):
        assert set(row["inputs"]) == {"id", "sql", "intent"}
        assert row["inputs"]["id"] == scenario.id
        assert row["outputs"] == {"expected_decision": scenario.expected_decision}


async def test_three_experiments_run_with_the_right_prefixes_and_metadata() -> None:
    aevaluate, opened = FakeAevaluate(), []
    runs = await run_all(aevaluate, FakePipeline(), opened)
    assert [c["experiment_prefix"] for c in aevaluate.calls] == [
        "rules-only",
        "rules+judge",
        "rules+judge-no-rag",
    ]
    assert opened == ["rag", "no-rag"]
    assert [r.name for r in runs] == [
        "rules-only-abc123",
        "rules+judge-abc123",
        "rules+judge-no-rag-abc123",
    ]
    for call in aevaluate.calls:
        assert call["data"] == ls.dataset_name(LABELED)
        assert call["metadata"]["summary_model"] == "google_genai:summary-x"
        assert call["metadata"]["judge_model"] == "google_genai:judge-y"
        assert call["max_concurrency"] == 1
        assert [f.__name__ for f in call["evaluators"]] == ["correct_label", "caught"]
        assert [f.__name__ for f in call["summary_evaluators"]] == [
            "catch_rate",
            "false_block_rate",
        ]


async def test_the_metadata_holds_no_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "key-value-1234")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-value-5678")
    aevaluate = FakeAevaluate()
    await run_all(aevaluate, FakePipeline())
    text = repr([c["metadata"] for c in aevaluate.calls])
    assert "1234" not in text
    assert "5678" not in text
    assert "postgresql" not in text


async def test_a_target_that_raises_is_recorded_not_raised() -> None:
    aevaluate = FakeAevaluate()
    runs = await run_all(aevaluate, FakePipeline(raises=True))
    assert all(r.error is None for r in runs)
    failed = [o for o in aevaluate.outputs if o["error"]]
    assert failed
    assert all(o["decision"] is None for o in failed)
    # The class name only: the exception text can hold SQL.
    assert all("secret-sql-text" not in str(o) for o in failed)


async def test_a_failing_experiment_is_recorded_and_the_next_still_runs() -> None:
    aevaluate = FakeAevaluate(fail_on="rules+judge")
    runs = await run_all(aevaluate, FakePipeline())
    assert [r.error is not None for r in runs] == [False, True, False]
    assert runs[1].error == "RuntimeError"
    assert len(aevaluate.calls) == 3


def test_the_evaluators_score_what_they_say() -> None:
    reference = {"expected_decision": "review"}
    assert ls.correct_label({"decision": "review"}, reference)["score"] == 1
    assert ls.correct_label({"decision": "block"}, reference)["score"] == 0
    assert ls.caught({"decision": "block"})["score"] == 1
    assert ls.caught({"decision": "allow"})["score"] == 0
    assert ls.caught({"decision": None})["score"] == 0


def test_the_summary_evaluators_use_the_right_denominators() -> None:
    outputs = [
        {"decision": "block"},
        {"decision": "allow"},
        {"decision": "review"},
        {"decision": None},
    ]
    reference = [
        {"expected_decision": "block"},
        {"expected_decision": "review"},
        {"expected_decision": "allow"},
        {"expected_decision": "block"},
    ]
    # Dangerous and measured: block->block and review->allow, so 1 of 2.
    assert ls.catch_rate(outputs, reference)["score"] == 0.5
    # Safe and measured: allow->review, so 1 of 1.
    assert ls.false_block_rate(outputs, reference)["score"] == 1.0
