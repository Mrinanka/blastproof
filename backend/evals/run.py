"""The eval suite (backend plan §14): `uv run python -m evals.run`.

`--mode rules` (the default) replays the incident and labeled scenarios through the policy rules and
the hook rules only: no Docker, no model, deterministic. CI runs it on every pull request.

`--mode live` runs the real pipeline with the configured models against the local Compose database
(see `evals/live_mode.py`). Run it with the model key in the environment:
`uv run --env-file .env python -m evals.run --mode live`.

`--langsmith` (live only) also pushes the labeled set to LangSmith and runs three experiments.

Exit code: 0 when every incident replay is caught and nothing errored; 1 when one was missed or a
scenario could not be measured (D-045); 2 when the run was refused or misconfigured. The false block
rate has no threshold.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Coroutine, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import DEFAULT_POLICY_FILE, Settings
from app.logging import configure_logging
from app.services.policy import load_policy
from evals.metrics import Outcome, gate_failures, summarize
from evals.report import format_table, write_json
from evals.rules_mode import EvalError, error_text, run_rules

EVALS_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
RESULTS_DIR = EVALS_DIR / "results"
DEFAULT_SCENARIOS = (SCENARIOS_DIR / "incidents.yaml", SCENARIOS_DIR / "labeled.yaml")
LABELED = SCENARIOS_DIR / "labeled.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals.run", description="Run the Blastproof evals.")
    parser.add_argument("--mode", choices=("rules", "live"), default="rules")
    parser.add_argument(
        "--out", type=Path, help="JSON results file (default evals/results/<mode>.json)"
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        action="append",
        help="a scenario file (repeatable; default: both)",
    )
    parser.add_argument(
        "--langsmith", action="store_true", help="live only: run LangSmith experiments"
    )
    parser.add_argument(
        "--reads-repeat", type=int, default=20, help="live: repetitions per read query"
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="live: seconds per scenario")
    return parser


def _run_on_selector_loop[T](coro: Coroutine[object, object, T]) -> T:
    # psycopg's async mode (the checkpointer) cannot use Windows' Proactor loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop_factory)


async def _live(
    settings: Settings, files: Sequence[Path], args: argparse.Namespace
) -> tuple[list[Outcome], list[str]]:
    from evals.live_mode import measure_reads, run_live

    outcomes = await run_live(settings, files, timeout_seconds=args.timeout)
    outcomes += await measure_reads(settings, repeat=args.reads_repeat)
    notes: list[str] = []
    if args.langsmith:
        from langsmith import Client
        from langsmith.evaluation import aevaluate

        from evals.langsmith_experiments import run_experiments

        runs = await run_experiments(
            Client(),
            aevaluate,
            settings=settings,
            policy=load_policy(settings.policy_file),
            labeled_path=LABELED,
            timeout_seconds=args.timeout,
        )
        notes = [f"LangSmith {r.variant}: {r.name or 'FAILED: ' + str(r.error)}" for r in runs]
    return outcomes, notes


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.langsmith and args.mode != "live":
        parser.error("--langsmith needs --mode live")
    files = tuple(args.scenarios or DEFAULT_SCENARIOS)
    out: Path = args.out or RESULTS_DIR / f"{args.mode}.json"
    meta: dict[str, Any] = {"mode": args.mode, "at": datetime.now(UTC).isoformat()}
    notes: list[str] = []

    if args.mode == "rules":
        outcomes = run_rules(files, load_policy(DEFAULT_POLICY_FILE))
    else:
        from evals.live_mode import LiveModeError, check_live_settings

        try:
            settings = Settings()
        except ValidationError as exc:
            # Field names only: a validation message can echo a value.
            names = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
            print(f"Error: the settings are invalid: {', '.join(names)}.", file=sys.stderr)
            return 2
        configure_logging(settings.log_level, stream=sys.stderr)
        if args.langsmith and not os.environ.get("LANGSMITH_API_KEY"):
            print("Error: --langsmith needs LANGSMITH_API_KEY in the environment.", file=sys.stderr)
            return 2
        try:
            check_live_settings(settings)
            outcomes, notes = _run_on_selector_loop(_live(settings, files, args))
        except LiveModeError as exc:
            print(f"Error: {error_text(exc)}", file=sys.stderr)
            return 2
        except EvalError as exc:
            print(f"Error: {error_text(exc)}", file=sys.stderr)
            return 1
        finally:
            _flush_traces(args.langsmith)
        meta |= {"summary_model": settings.summary_model, "judge_model": settings.judge_model}

    metrics = summarize(outcomes)
    print(format_table(metrics))
    for note in notes:
        print(note)
    write_json(metrics, outcomes, out, meta=meta)
    print(f"\nResults: {out}")
    failures = gate_failures(metrics, outcomes)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


def _flush_traces(enabled: bool) -> None:
    """Send any queued LangSmith traces before the process exits."""
    if not enabled and os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        return
    from langchain_core.tracers.langchain import wait_for_all_tracers

    wait_for_all_tracers()


if __name__ == "__main__":
    sys.exit(main())
