"""The graph state (D-178): plain JSON types only, and the helpers that turn the service
dataclasses into them and back.

Two rules shape this file:

* Raw agent text never enters the state. LangGraph writes the state to every checkpoint and traces
  node inputs and outputs, so `sql`, `pinned_sql` and the raw intent stay out (SEC-14, ARCH-17).
  The state holds `sql_display` and masked text; a node that needs `pinned_sql` reads `actions`.
* The state stays small. The per-row changes of a shadow run (about 246k for the Replit delete) go
  to `preview_rows`; the state holds only `Diff.impact_json()` and `Diff.cascade_json()`.
"""

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Literal, TypedDict

from app.db.enums import Decision
from app.services.classifier import Classification, Kind
from app.services.company_schema import ForeignKey
from app.services.judge import JudgeResult, JudgeVerdict
from app.services.knowledge import KnowledgeRef, Retrieval, RetrievedChunk
from app.services.masking import mask_text
from app.services.policy import PolicyDecision, RuleMatch
from app.services.shadow import CascadePath, Diff, SampleRow, TableDiff

RunOutcome = Literal["failed", "mismatch"]


class WriteState(TypedDict, total=False):
    """Every key is JSON-safe. `total=False`: a node returns only what it changes."""

    action_id: str
    agent_id: str
    profile: str
    intent_masked: str
    classification: dict[str, Any]  # `pinned_sql` is blanked
    route: str  # a `policy.Route` value
    diff: dict[str, Any]  # {"impact": Diff.impact_json(), "cascade": Diff.cascade_json()}
    pre_rules: list[dict[str, str]]
    post_rules: list[dict[str, str]]
    post_decision: dict[str, str]  # {"decision", "reason"}: the pass-2 result
    knowledge: dict[str, Any] | None
    knowledge_failed: bool
    impact_report: dict[str, Any]
    judge: dict[str, Any]
    # The final decision (allow | review | block), why, and who decided it.
    decision: str
    reason: str
    deciding: str
    ai_unavailable: bool
    injection: list[str]
    decided: bool  # the `decided` audit event is written
    review: dict[str, Any]  # the resume value
    work_ms: int  # Blastproof's own time: the sum of node durations, never the review wait
    drifts: int  # commits that found the data changed since the preview (a second one fails)
    repreview: bool  # a drift sent the run back to `shadow`; `shadow` clears it
    outcome: RunOutcome


# --- Classification -----------------------------------------------------------------------------


def classification_to_state(c: Classification) -> dict[str, Any]:
    data = asdict(c)
    data["kind"] = c.kind.value
    data["pinned_sql"] = ""
    for name, value in data.items():
        if isinstance(value, tuple):
            data[name] = list(value)
    return data


def classification_from_state(data: dict[str, Any], pinned_sql: str = "") -> Classification:
    """Back to a `Classification`. `pinned_sql` is not in the state: pass it when it is needed."""
    values = dict(data)
    values["kind"] = Kind(values["kind"])
    values["pinned_sql"] = pinned_sql
    for name in (
        "statement_types",
        "tables",
        "target_tables",
        "schemas",
        "side_effect_functions",
        "unknown_functions",
    ):
        values[name] = tuple(values[name])
    return Classification(**values)


# --- Diff ---------------------------------------------------------------------------------------


def diff_to_state(diff: Diff) -> dict[str, Any]:
    return {"impact": diff.impact_json(), "cascade": diff.cascade_json()}


def _cascade_path(item: dict[str, Any]) -> CascadePath:
    """A path rebuilt from its JSON. The foreign keys keep only the table names (nearest the
    target first): that is all the summary and the judge read."""
    names = [*item["via"], item["table"]]
    parents = [item["target"], *item["via"]]
    edges = tuple(
        ForeignKey(
            child=child,
            child_columns=(),
            parent=parent,
            parent_columns=(),
            on_delete=item["on_delete"],
        )
        for child, parent in zip(names, parents, strict=True)
    )
    return CascadePath(item["target"], edges, item["on_delete"], item["count"])


def diff_from_state(data: dict[str, Any]) -> Diff:
    """A `Diff` without row changes (`changes=()`): the summary, rules and judge never read them."""
    impact = data["impact"]
    changes = impact["table_changes"]
    cascade_tables = set(impact["cascade_tables"])
    tables = {
        name: TableDiff(
            table=name,
            deleted=changes.get(name, {}).get("deleted", 0),
            updated=changes.get(name, {}).get("updated", 0),
            inserted=changes.get(name, {}).get("inserted", 0),
            rows_before=rows_before,
            is_cascade=name in cascade_tables,
        )
        for name, rows_before in impact["rows_before"].items()
    }
    return Diff(
        tables=tables,
        breakdowns={key: dict(counts) for key, counts in impact["breakdowns"].items()},
        derived_groups=dict(impact["derived_groups"]),
        cascade_expectations=tuple(_cascade_path(item) for item in data["cascade"]),
        samples=tuple(SampleRow(**sample) for sample in impact["samples"]),
    )


# --- Knowledge ----------------------------------------------------------------------------------


def retrieval_to_state(retrieval: Retrieval) -> dict[str, Any]:
    """The chunks are masked on the way in: the checkpoint is not the place for an email address
    an uploaded note happens to hold (the summary and the judge mask again, harmlessly)."""
    return {
        "chunks": [
            {**asdict(chunk), "content": mask_text(chunk.content)} for chunk in retrieval.chunks
        ],
        "refs": [asdict(ref) for ref in retrieval.refs],
        "duration_ms": retrieval.duration_ms,
    }


def retrieval_from_state(data: dict[str, Any] | None) -> Retrieval | None:
    if data is None:
        return None
    return Retrieval(
        chunks=[RetrievedChunk(**chunk) for chunk in data["chunks"]],
        refs=[KnowledgeRef(**ref) for ref in data["refs"]],
        duration_ms=data["duration_ms"],
    )


# --- Rules and the judge ------------------------------------------------------------------------


def rules_to_state(matched: tuple[RuleMatch, ...]) -> list[dict[str, str]]:
    return [{"name": m.name, "outcome": m.outcome.value, "reason": m.reason} for m in matched]


def policy_decision_to_state(decision: PolicyDecision) -> dict[str, str]:
    return {"decision": decision.decision.value, "reason": decision.reason}


def policy_decision_from_state(
    decision: dict[str, str], matched: list[dict[str, str]]
) -> PolicyDecision:
    return PolicyDecision(
        Decision(decision["decision"]),
        tuple(RuleMatch(m["name"], Decision(m["outcome"]), m["reason"]) for m in matched),
        decision["reason"],
    )


def matched_rules_json(
    pre: list[dict[str, str]], post: list[dict[str, str]]
) -> list[dict[str, str]]:
    """For `actions.matched_rules`: `[{name, outcome, reason, pass}]`, `pass` being `pre` or `post`
    (D-171). The actions views read exactly this shape."""
    return [{**rule, "pass": "pre"} for rule in pre] + [{**rule, "pass": "post"} for rule in post]


def judge_from_state(data: dict[str, Any]) -> JudgeResult:
    """The inverse of `JudgeResult.to_json()`."""
    cost = data["cost_usd"]
    return JudgeResult(
        provider=data["provider"],
        model=data["model"],
        verdict=None if data["result"] is None else JudgeVerdict(**data["result"]),
        failure=data["failure"],
        reason_withheld=data["reason_withheld"],
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        cost_usd=None if cost is None else Decimal(cost),
        duration_ms=data["duration_ms"],
    )
