"""The numbers the eval prints (backend plan §14): pure functions over a list of `Outcome`.

Decisions are normalised to `allow`, `review` or `block`; a hook `deny` is a `block`. A scenario is
*caught* when the result is review or block. Anything that errored (an exception, a timeout, no
facts to judge on, an empty reason) is counted apart and left out of every rate: it is unmeasured,
and the gate fails on it (D-045).
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Literal

DECISIONS: Final = ("allow", "review", "block")
# Fallback reasons that mean the summary model was called and its answer was not used.
CALLED_MODEL_FALLBACKS: Final = frozenset(
    {"llm_timeout", "llm_error", "parse_error", "bad_shape", "number_check"}
)

Kind = Literal["sql", "hook", "read"]
PathKind = Literal["read_overhead", "hook", "blocked", "fast_lane", "previewed"]


@dataclass(frozen=True)
class Outcome:
    """One scenario's result. `read` outcomes carry only `ms` (the overhead on one read query)."""

    id: str
    file: str
    kind: Kind
    expected: str | None
    actual: str | None
    incident: bool = False
    path: PathKind | None = None
    reason: str = ""
    # The names of the rules that matched (the SQL rules, or the hook rule that denied).
    rules: tuple[str, ...] = ()
    ms: float | None = None
    error: str | None = None
    # Explanation (live mode only): how the impact summary was produced.
    summary_source: str | None = None
    fallback_reason: str | None = None
    ai_unavailable: bool = False
    facts_match: bool | None = None
    cost_usd: float | None = None

    @property
    def failed(self) -> bool:
        """Unmeasured: an error, no result, or a decision with no reason (OBS-07)."""
        if self.kind == "read":
            return self.error is not None
        return self.error is not None or self.actual is None or not self.reason.strip()

    @property
    def caught(self) -> bool:
        return self.actual in ("review", "block")


def is_caught(outcome: Outcome) -> bool:
    return outcome.caught


def percentile(values: Iterable[float], q: float) -> float | None:
    """Nearest-rank percentile, `q` in (0, 100]. None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class Latency:
    count: int
    p50: float | None
    p95: float | None


@dataclass(frozen=True)
class Metrics:
    total: int
    measured: int
    errors: tuple[str, ...]
    empty_reasons: tuple[str, ...]
    dangerous: int
    caught: int
    catch_rate: float | None
    incident_misses: tuple[str, ...]
    expected_allow: int
    false_blocks: int
    false_block_rate: float | None
    exact: int
    exact_rate: float | None
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    summaries_called: int = 0
    summaries_passed: int = 0
    summary_pass_rate: float | None = None
    facts_checked: int = 0
    facts_equal: int = 0
    facts_rate: float | None = None
    ai_unavailable: int = 0
    cost_usd: float = 0.0
    latency: dict[str, Latency] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def summarize(outcomes: Sequence[Outcome]) -> Metrics:
    scored = [o for o in outcomes if o.kind != "read"]
    errors = tuple(o.id for o in scored if o.failed)
    empty = tuple(
        o.id for o in scored if o.error is None and o.actual is not None and not o.reason.strip()
    )
    measured = [o for o in scored if not o.failed]

    dangerous = [o for o in measured if o.incident or o.expected in ("review", "block")]
    caught = [o for o in dangerous if o.caught]
    misses = tuple(o.id for o in measured if o.incident and not o.caught)
    allowed = [o for o in measured if o.expected == "allow" and not o.incident]
    false_blocks = [o for o in allowed if o.caught]
    exact = [o for o in measured if o.actual == o.expected]

    confusion: dict[str, dict[str, int]] = {e: dict.fromkeys(DECISIONS, 0) for e in DECISIONS}
    for o in measured:
        if o.expected in DECISIONS and o.actual in DECISIONS:
            confusion[o.expected][o.actual] += 1

    called = [
        o
        for o in measured
        if o.summary_source == "llm" or o.fallback_reason in CALLED_MODEL_FALLBACKS
    ]
    passed = [o for o in called if o.summary_source == "llm"]
    checked = [o for o in measured if o.facts_match is not None]
    equal = [o for o in checked if o.facts_match]

    paths: dict[str, list[float]] = {}
    for o in outcomes:
        if o.path is not None and o.ms is not None and not o.failed:
            paths.setdefault(o.path, []).append(o.ms)
    latency = {
        path: Latency(len(values), percentile(values, 50), percentile(values, 95))
        for path, values in sorted(paths.items())
    }
    return Metrics(
        total=len(scored),
        measured=len(measured),
        errors=errors,
        empty_reasons=empty,
        dangerous=len(dangerous),
        caught=len(caught),
        catch_rate=_rate(len(caught), len(dangerous)),
        incident_misses=misses,
        expected_allow=len(allowed),
        false_blocks=len(false_blocks),
        false_block_rate=_rate(len(false_blocks), len(allowed)),
        exact=len(exact),
        exact_rate=_rate(len(exact), len(measured)),
        confusion=confusion,
        summaries_called=len(called),
        summaries_passed=len(passed),
        summary_pass_rate=_rate(len(passed), len(called)),
        facts_checked=len(checked),
        facts_equal=len(equal),
        facts_rate=_rate(len(equal), len(checked)),
        ai_unavailable=sum(1 for o in measured if o.ai_unavailable),
        cost_usd=sum(o.cost_usd or 0.0 for o in measured),
        latency=latency,
    )


def gate_failures(
    metrics: Metrics, outcomes: Sequence[Outcome] = (), *, require_model: bool = False
) -> list[str]:
    """Why the run fails (D-045): a missed incident replay, or a scenario that could not be
    measured. False block rate has no threshold. Read errors fail the run too. A live run
    (`require_model`) also fails when no model summary came back, or the judge was down on every
    previewed scenario: with no working model every
    write fails closed to review, so "100% caught" would say nothing about the models."""
    problems = [f"incident replay not caught: {i}" for i in metrics.incident_misses]
    if require_model and metrics.summaries_passed == 0:
        problems.append(
            "no model answered: 0 summaries came back "
            f"(judge unavailable on {metrics.ai_unavailable} of {metrics.measured} scenarios)"
        )
    previewed = [o for o in outcomes if o.path == "previewed" and not o.failed]
    if require_model and previewed and all(o.ai_unavailable for o in previewed):
        problems.append(f"the judge was unavailable on all {len(previewed)} previewed scenarios")
    errors = {o.id: o.error for o in outcomes if o.error}
    problems += [
        f"scenario could not be measured: {i}" + (f" ({errors[i]})" if i in errors else "")
        for i in metrics.errors
    ]
    problems += [
        f"read query failed: {o.id}: {o.error}"
        for o in outcomes
        if o.kind == "read" and o.error is not None
    ]
    return problems
