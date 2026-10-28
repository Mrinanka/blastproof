"""`--langsmith`: push the labeled set to LangSmith and run three experiments on it (EVAL-09).

The dataset is named after the file's hash (`blastproof-labeled-<sha8>`) and created only when it is
missing, so every experiment compares on identical data. The experiments are `rules-only`,
`rules+judge` and `rules+judge-no-rag`. Evaluators: `correct_label` and `caught` per example;
summary evaluators `catch_rate` and `false_block_rate`. Experiment metadata holds model names from
settings, never keys.

The client, `aevaluate` and the pipeline factory are injectable, so the tests need no network.
"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import structlog

from app.config import Settings
from app.services.policy import Policy
from evals.live_mode import JudgeVariant, LivePipeline, live_pipeline
from evals.rules_mode import anchor_time, error_text, sql_outcome
from evals.schema import SqlScenario, load_scenario_file

logger = structlog.get_logger()

DATASET_PREFIX = "blastproof-labeled-"
VARIANTS: tuple[tuple[str, JudgeVariant | None], ...] = (
    ("rules-only", None),
    ("rules+judge", "rag"),
    ("rules+judge-no-rag", "no-rag"),
)
CAUGHT = ("review", "block")

Target = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
PipelineFactory = Callable[[JudgeVariant], AbstractAsyncContextManager[LivePipeline]]


def dataset_name(path: Path) -> str:
    return DATASET_PREFIX + hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def example_rows(scenarios: Sequence[SqlScenario]) -> list[dict[str, Any]]:
    return [
        {
            "inputs": {"id": s.id, "sql": s.sql, "intent": s.intent},
            "outputs": {"expected_decision": s.expected_decision},
            "metadata": {"decidable_from_sql": s.decidable_from_sql},
        }
        for s in scenarios
    ]


def ensure_dataset(client: Any, path: Path, scenarios: Sequence[SqlScenario]) -> str:
    """Create the dataset and its examples unless it exists; returns its name."""
    name = dataset_name(path)
    if client.has_dataset(dataset_name=name):
        return name
    client.create_dataset(
        dataset_name=name,
        description=f"Blastproof labeled actions from {path.name} (backend plan §14).",
    )
    client.create_examples(dataset_name=name, examples=example_rows(scenarios))
    return name


# --- evaluators ---------------------------------------------------------------------------------


def correct_label(
    outputs: Mapping[str, Any], reference_outputs: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "key": "correct_label",
        "score": int(outputs.get("decision") == reference_outputs.get("expected_decision")),
    }


def caught(outputs: Mapping[str, Any]) -> dict[str, Any]:
    """1 when the write was held or blocked, 0 when it was allowed or the target errored."""
    return {"key": "caught", "score": int(outputs.get("decision") in CAUGHT)}


def catch_rate(
    outputs: Sequence[Mapping[str, Any]], reference_outputs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    pairs = [
        o.get("decision")
        for o, r in zip(outputs, reference_outputs, strict=True)
        if r.get("expected_decision") in CAUGHT and o.get("decision") is not None
    ]
    return {
        "key": "catch_rate",
        "score": sum(d in CAUGHT for d in pairs) / len(pairs) if pairs else 0,
    }


def false_block_rate(
    outputs: Sequence[Mapping[str, Any]], reference_outputs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    pairs = [
        o.get("decision")
        for o, r in zip(outputs, reference_outputs, strict=True)
        if r.get("expected_decision") == "allow" and o.get("decision") is not None
    ]
    score = sum(d in CAUGHT for d in pairs) / len(pairs) if pairs else 0
    return {"key": "false_block_rate", "score": score}


# --- targets ------------------------------------------------------------------------------------


def _result(decision: str | None, reason: str, error: str | None) -> dict[str, Any]:
    return {"decision": decision, "reason": reason, "error": error}


def make_rules_target(
    scenarios: Mapping[str, SqlScenario], policy: Policy, anchor: date, file: str
) -> Target:
    now = anchor_time(anchor)

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            outcome = sql_outcome(scenarios[inputs["id"]], file, policy, now)
        except Exception as exc:
            return _result(None, "", error_text(exc))
        return _result(outcome.actual, outcome.reason, None)

    return target


def make_live_target(
    scenarios: Mapping[str, SqlScenario], pipeline: LivePipeline, file: str, timeout: float
) -> Target:
    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            outcome = await pipeline.run(scenarios[inputs["id"]], file, timeout)
        except Exception as exc:
            return _result(None, "", error_text(exc))
        return _result(outcome.actual, outcome.reason, outcome.error)

    return target


@dataclass(frozen=True)
class ExperimentRun:
    variant: str
    name: str | None
    error: str | None = None


async def run_experiments(
    client: Any,
    aevaluate: Callable[..., Awaitable[Any]],
    *,
    settings: Settings,
    policy: Policy,
    labeled_path: Path,
    timeout_seconds: float,
    pipeline: PipelineFactory | None = None,
) -> list[ExperimentRun]:
    """Upload the dataset if needed, then run the three experiments in order. A failing experiment
    is recorded and the next one still runs."""
    loaded = load_scenario_file(labeled_path)
    scenarios = {s.id: s for s in loaded.scenarios if isinstance(s, SqlScenario)}
    name = ensure_dataset(client, labeled_path, list(scenarios.values()))
    open_pipeline = pipeline or (
        lambda variant: live_pipeline(settings, loaded.anchor_date, judge_variant=variant)
    )
    runs: list[ExperimentRun] = []
    for prefix, variant in VARIANTS:
        metadata = {
            "variant": prefix,
            "dataset": name,
            "anchor_date": loaded.anchor_date.isoformat(),
            "summary_model": settings.summary_model,
            "judge_model": settings.judge_model,
        }
        try:
            if variant is None:
                result = await _evaluate(
                    aevaluate,
                    make_rules_target(scenarios, policy, loaded.anchor_date, labeled_path.name),
                    name,
                    prefix,
                    metadata,
                    client,
                )
            else:
                async with open_pipeline(variant) as live:
                    result = await _evaluate(
                        aevaluate,
                        make_live_target(scenarios, live, labeled_path.name, timeout_seconds),
                        name,
                        prefix,
                        metadata,
                        client,
                    )
            runs.append(ExperimentRun(prefix, getattr(result, "experiment_name", None)))
        except Exception as exc:
            logger.error("eval_experiment_failed", variant=prefix, error_class=type(exc).__name__)
            runs.append(ExperimentRun(prefix, None, error_text(exc)))
    return runs


async def _evaluate(
    aevaluate: Callable[..., Awaitable[Any]],
    target: Target,
    dataset: str,
    prefix: str,
    metadata: dict[str, str],
    client: Any,
) -> Any:
    return await aevaluate(
        target,
        data=dataset,
        evaluators=[correct_label, caught],
        summary_evaluators=[catch_rate, false_block_rate],
        experiment_prefix=prefix,
        metadata=metadata,
        # Live examples share one runner, one at a time.
        max_concurrency=1,
        client=client,
    )
