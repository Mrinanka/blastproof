"""Response and query models for the actions routes (backend plan §6, "Action detail contract").

Raw `sql` and `pinned_sql` have no field here: only the masked `sql_display` (and, when pinning
changed the statement, `original_sql_display`) can leave the backend. Users appear as `display_name`
only, never an email.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.db.enums import ActionStatus, Decision, RiskLevel

# What an audit event's `data` may hold in the API: scalars only, so nothing nested leaks.
JsonScalar = str | int | float | bool | None


class AgentRef(BaseModel):
    id: uuid.UUID
    name: str


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class ActionSummary(BaseModel):
    id: uuid.UUID
    status: ActionStatus
    decision: Decision | None
    risk_level: RiskLevel | None
    agent: AgentRef
    intent: str | None  # masked
    headline: str | None  # the impact report's headline, once there is one
    tables: list[str]
    created_at: datetime
    decided_by: UserRef | None


class ActionPage(BaseModel):
    items: list[ActionSummary]
    total: int
    limit: int
    offset: int


class ImpactReportView(BaseModel):
    headline: str
    key_points: list[str]
    concerns: list[str]
    source: Literal["llm", "template"]


class TableChange(BaseModel):
    table: str
    deleted: int
    updated: int
    inserted: int
    is_cascade: bool


class Breakdown(BaseModel):
    table: str
    column: str
    counts: dict[str, int]


class DerivedGroupView(BaseModel):
    table: str
    name: str
    count: int
    # None when the group is no longer in the rules file.
    definition: str | None


class SampleRowView(BaseModel):
    table: str
    change: Literal["deleted", "updated", "inserted"]
    side: Literal["before", "after"]
    values: dict[str, Any]  # masked before it was stored


class JudgeView(BaseModel):
    provider: str
    model: str | None
    # The verdict fields are all None when `failure` is set (the AI check was unavailable) and
    # `reason` is None when the judge's reason was withheld.
    recommendation: Decision | None
    risk_level: RiskLevel | None
    confidence: float | None
    reason: str | None
    failure: str | None


class MatchedRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    outcome: Decision
    reason: str
    pass_: Literal["pre", "post"] = Field(alias="pass")


class KnowledgeRefView(BaseModel):
    chunk_id: str
    source: str
    table_names: list[str]
    first_line: str  # masked
    match: Literal["table", "similar"]
    score: float | None


class AuditEventView(BaseModel):
    event_type: str
    created_at: datetime
    data: dict[str, JsonScalar]


class Timings(BaseModel):
    preview_ms: int | None
    total_ms: int | None


class ActionDetail(BaseModel):
    id: uuid.UUID
    status: ActionStatus
    decision: Decision | None
    risk_level: RiskLevel | None
    agent: AgentRef
    owner: UserRef
    intent: str | None  # masked
    sql_display: str | None
    pinned_values: dict[str, str]
    original_sql_display: str | None
    statement_type: str | None
    tables: list[str]
    impact_report: ImpactReportView | None
    table_changes: list[TableChange]
    breakdowns: list[Breakdown]
    derived_groups: list[DerivedGroupView]
    samples: list[SampleRowView]
    judge: JudgeView | None
    matched_rules: list[MatchedRule]
    knowledge_used: list[KnowledgeRefView]
    policy_reason: str | None
    reviewer_note: str | None
    retry_of: uuid.UUID | None
    retried_by: uuid.UUID | None
    timeline: list[AuditEventView]
    timings: Timings
    llm_cost_usd: float | None
    decided_by: UserRef | None
    created_at: datetime
    decided_at: datetime | None
    expires_at: datetime | None
    can_decide: bool


class ActionFilters(BaseModel):
    """Query parameters of `GET /v1/actions`. Unknown parameters are ignored.

    `from` is inclusive and `to` exclusive; both need a time zone (CONV-06).
    """

    status: list[ActionStatus] = Field(default_factory=list)
    agent_id: uuid.UUID | None = None
    table: str | None = Field(default=None, min_length=1, max_length=63)
    from_: AwareDatetime | None = Field(default=None, alias="from")
    to: AwareDatetime | None = None
    order: Literal["newest", "oldest"] = "newest"
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _range_is_forward(self) -> "ActionFilters":
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("from must be earlier than to")
        return self
