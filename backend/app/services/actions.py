"""Turns action rows into the API's views (backend plan §6).

Counts and flags come from the stored columns; nothing here asks a model. Text an agent wrote
(`intent`, the first line of a knowledge chunk) is masked on the way out. Stored JSON is parsed
strictly: a shape the writers (steps 10 to 14) would never produce is our bug, so it raises
`StoredShapeError`, which the request answers with the 500 envelope, and the log names the field.
"""

from datetime import datetime
from typing import Any, Final

import structlog
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.db.actions import DetailRows, SummaryRow
from app.db.enums import ActionStatus, Decision, RiskLevel
from app.schemas.actions import (
    ActionDetail,
    ActionSummary,
    AgentRef,
    AuditEventView,
    Breakdown,
    DerivedGroupView,
    ImpactReportView,
    JsonScalar,
    JudgeView,
    KnowledgeRefView,
    MatchedRule,
    SampleRowView,
    TableChange,
    Timings,
    UserRef,
)
from app.services.classifier import display_in_worker
from app.services.masking import mask_text
from app.services.policy import Policy

logger = structlog.get_logger()

MAX_SAMPLES: Final = 50
MAX_TIMELINE_TEXT: Final = 200


class StoredShapeError(RuntimeError):
    """A stored JSON column does not have the shape its writer produces."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


class _Counts(BaseModel):
    deleted: int
    updated: int
    inserted: int


class _Impact(BaseModel):
    """What `Diff.impact_json()` writes; the keys this view does not show are ignored."""

    table_changes: dict[str, _Counts]
    cascade_tables: list[str]
    breakdowns: dict[str, dict[str, int]]
    derived_groups: dict[str, int]
    samples: list[SampleRowView]


class _Verdict(BaseModel):
    recommendation: Decision
    risk_level: RiskLevel
    confidence: float
    reason: str


class _Judge(BaseModel):
    """What `JudgeResult.to_json()` writes."""

    provider: str
    model: str | None
    result: _Verdict | None
    failure: str | None
    reason_withheld: bool


class _KnowledgeRef(BaseModel):
    """What `asdict(KnowledgeRef)` writes."""

    chunk_id: str
    source: str
    table_names: list[str]
    first_line: str
    match: str
    score: float | None


def _parse[T](field: str, parse: Any, value: Any) -> T:
    try:
        return parse(value)
    except ValidationError as exc:
        # Which part is wrong, never what it held: stored values may be personal data.
        locations = sorted({".".join(str(p) for p in e["loc"][:3]) for e in exc.errors()})
        logger.error("stored_shape_invalid", field=field, locations=locations[:5])
        raise StoredShapeError(field) from None


def can_decide(status: str, expires_at: datetime | None, now: datetime) -> bool:
    """A reviewer may decide only while the action waits for review and has not expired."""
    return status == ActionStatus.AWAITING_REVIEW and (expires_at is None or expires_at > now)


def build_summary(row: SummaryRow) -> ActionSummary:
    return ActionSummary(
        id=row.id,
        status=ActionStatus(row.status),
        decision=Decision(row.decision) if row.decision else None,
        risk_level=RiskLevel(row.risk_level) if row.risk_level else None,
        agent=AgentRef(id=row.agent_id, name=row.agent_name),
        intent=mask_text(row.intent) if row.intent is not None else None,
        headline=row.headline,
        tables=list(row.tables),
        created_at=row.created_at,
        decided_by=(
            UserRef(id=row.decider_id, display_name=row.decider_name or "")
            if row.decider_id is not None
            else None
        ),
    )


def timeline_data(data: dict[str, Any]) -> dict[str, JsonScalar]:
    """An audit event's data for the API: scalar values only, long text cut and masked."""
    kept: dict[str, JsonScalar] = {}
    for key, value in data.items():
        if isinstance(value, str):
            kept[key] = mask_text(value[:MAX_TIMELINE_TEXT])
        elif value is None or isinstance(value, bool | int | float):
            kept[key] = value
    return kept


def _derived_groups(policy: Policy, counts: dict[str, int]) -> list[DerivedGroupView]:
    views = []
    for key, count in sorted(counts.items()):
        table, dot, name = key.partition(".")
        if not dot or not name:
            logger.error("stored_shape_invalid", field="impact.derived_groups", locations=["key"])
            raise StoredShapeError("impact.derived_groups")
        group = policy.derived_groups.get(table, {}).get(name)
        views.append(
            DerivedGroupView(
                table=table,
                name=name,
                count=count,
                definition=group.condition if group is not None else None,
            )
        )
    return views


def _breakdowns(stored: dict[str, dict[str, int]]) -> list[Breakdown]:
    views = []
    for key, counts in sorted(stored.items()):
        table, dot, column = key.partition(".")
        if not dot or not column:
            logger.error("stored_shape_invalid", field="impact.breakdowns", locations=["key"])
            raise StoredShapeError("impact.breakdowns")
        views.append(Breakdown(table=table, column=column, counts=counts))
    return views


def _judge_view(stored: Any) -> JudgeView | None:
    if stored is None:
        return None
    judge = _parse("judge", _Judge.model_validate, stored)
    verdict = judge.result
    return JudgeView(
        provider=judge.provider,
        model=judge.model,
        recommendation=verdict.recommendation if verdict else None,
        risk_level=verdict.risk_level if verdict else None,
        confidence=verdict.confidence if verdict else None,
        reason=verdict.reason if verdict and not judge.reason_withheld else None,
        failure=judge.failure,
    )


async def build_detail(rows: DetailRows, policy: Policy, now: datetime) -> ActionDetail:
    action = rows.action
    impact = (
        _parse("impact", _Impact.model_validate, action.impact)
        if action.impact is not None
        else None
    )
    report = (
        _parse("impact_report", ImpactReportView.model_validate, action.impact_report)
        if action.impact_report is not None
        else None
    )
    matched = _parse(
        "matched_rules",
        TypeAdapter(list[MatchedRule]).validate_python,
        action.matched_rules or [],
    )
    knowledge = _parse(
        "knowledge_used",
        TypeAdapter(list[_KnowledgeRef]).validate_python,
        action.knowledge_used or [],
    )
    pinned_values = dict(action.pinned_values or {})
    # Shown only when pinning changed the statement, and computed here: it is not stored.
    original = (
        await display_in_worker(action.sql) if pinned_values and action.sql is not None else None
    )
    table_changes = (
        sorted(
            (
                TableChange(
                    table=name,
                    deleted=c.deleted,
                    updated=c.updated,
                    inserted=c.inserted,
                    is_cascade=name in impact.cascade_tables,
                )
                for name, c in impact.table_changes.items()
            ),
            key=lambda t: (t.is_cascade, t.table),
        )
        if impact
        else []
    )
    return ActionDetail(
        id=action.id,
        status=ActionStatus(action.status),
        decision=Decision(action.decision) if action.decision else None,
        risk_level=RiskLevel(action.risk_level) if action.risk_level else None,
        agent=AgentRef(id=rows.agent.id, name=rows.agent.name),
        owner=UserRef(id=rows.owner.id, display_name=rows.owner.display_name),
        intent=mask_text(action.intent) if action.intent is not None else None,
        sql_display=action.sql_display,
        pinned_values=pinned_values,
        original_sql_display=original,
        statement_type=action.statement_type,
        tables=list(action.tables),
        impact_report=report,
        table_changes=table_changes,
        breakdowns=_breakdowns(impact.breakdowns) if impact else [],
        derived_groups=_derived_groups(policy, impact.derived_groups) if impact else [],
        samples=impact.samples[:MAX_SAMPLES] if impact else [],
        judge=_judge_view(action.judge),
        matched_rules=matched,
        knowledge_used=[
            KnowledgeRefView(
                chunk_id=ref.chunk_id,
                source=ref.source,
                table_names=ref.table_names,
                first_line=mask_text(ref.first_line),
                match=ref.match,  # type: ignore[arg-type]
                score=ref.score,
            )
            for ref in knowledge
        ],
        policy_reason=action.policy_reason,
        reviewer_note=action.reviewer_note,
        retry_of=action.retry_of,
        retried_by=rows.retried_by,
        timeline=[
            AuditEventView(
                event_type=event.event_type,
                created_at=event.created_at,
                data=timeline_data(event.data),
            )
            for event in rows.events
        ],
        timings=Timings(preview_ms=action.preview_ms, total_ms=action.total_ms),
        llm_cost_usd=float(action.llm_cost_usd) if action.llm_cost_usd is not None else None,
        decided_by=(
            UserRef(id=rows.decider.id, display_name=rows.decider.display_name)
            if rows.decider is not None
            else None
        ),
        created_at=action.created_at,
        decided_at=action.decided_at,
        expires_at=action.expires_at,
        can_decide=can_decide(action.status, action.expires_at, now),
    )
