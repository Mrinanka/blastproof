"""The eval's output: a plain-text table for the terminal and a JSON file for CI and later runs.

The JSON holds scenario ids, labels, results, reasons, timings and model names. It never holds SQL
text, intents, connection URLs or keys.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.metrics import DECISIONS, Metrics, Outcome


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def format_table(metrics: Metrics) -> str:
    m = metrics
    lines = [
        f"Scenarios: {m.total} ({m.measured} measured, {len(m.errors)} errors)",
        f"Catch rate:        {_pct(m.catch_rate)}  ({m.caught} of {m.dangerous} dangerous)",
        f"False block rate:  {_pct(m.false_block_rate)}"
        f"  ({m.false_blocks} of {m.expected_allow} safe)",
        f"Exact label match: {_pct(m.exact_rate)}  ({m.exact} of {m.measured})",
    ]
    if m.summaries_called or m.facts_checked:
        lines += [
            f"Summaries passing the number check: {_pct(m.summary_pass_rate)}"
            f"  ({m.summaries_passed} of {m.summaries_called} model summaries)",
            f"Measured facts equal to the labels: {_pct(m.facts_rate)}"
            f"  ({m.facts_equal} of {m.facts_checked})",
            f"Judge unavailable: {m.ai_unavailable}   Cost: ${m.cost_usd:.4f}",
        ]
    lines += ["", r"Expected \ got   " + "  ".join(f"{d:>7}" for d in DECISIONS)]
    for expected in DECISIONS:
        row = m.confusion.get(expected, {})
        lines.append(f"{expected:<15} " + "  ".join(f"{row.get(d, 0):>7}" for d in DECISIONS))
    if m.latency:
        lines += ["", f"{'Path':<15} {'n':>4}  {'p50':>10}  {'p95':>10}"]
        for path, lat in m.latency.items():
            lines.append(f"{path:<15} {lat.count:>4}  {_ms(lat.p50):>10}  {_ms(lat.p95):>10}")
    if m.incident_misses:
        lines += ["", "Incident replays NOT caught: " + ", ".join(m.incident_misses)]
    if m.errors:
        lines += ["", "Could not be measured: " + ", ".join(m.errors)]
    return "\n".join(lines)


def outcome_row(o: Outcome) -> dict[str, Any]:
    return {
        "id": o.id,
        "file": o.file,
        "kind": o.kind,
        "expected": o.expected,
        "actual": o.actual,
        "incident": o.incident,
        "path": o.path,
        "reason": o.reason,
        "rules": list(o.rules),
        "ms": o.ms,
        "error": o.error,
        "summary_source": o.summary_source,
        "fallback_reason": o.fallback_reason,
        "ai_unavailable": o.ai_unavailable,
        "facts_match": o.facts_match,
        "cost_usd": o.cost_usd,
    }


def write_json(
    metrics: Metrics,
    outcomes: Sequence[Outcome],
    path: Path,
    *,
    meta: Mapping[str, Any] | None = None,
) -> None:
    document = {
        "meta": dict(meta or {}),
        "metrics": metrics.as_dict(),
        "outcomes": [outcome_row(o) for o in outcomes],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
