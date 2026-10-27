"""What the agent is told about an action, from its stored state (backend plan §6, step 17).

Pure code: no database, no clock of its own. `query_database` and `get_action_status` both read an
`AgentView` and call `outcome_for`, so the two tools can never disagree about the same action. The
numbers come from what was stored (the committed row count, or the computed impact), never from a
model (CLAUDE.md: code counts, the LLM explains).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db.enums import ActionStatus
from app.services import agent_messages

INTERNAL_ERROR = "internal_error"
# What `busy` tells the agent when the cause is not a rate limit (no slot, drain, recovery).
BUSY_RETRY_SECONDS = 10

_S = ActionStatus
# Nothing for the agent to act on yet: Blastproof is still working on it (decision C).
_IN_PROGRESS = frozenset(
    {_S.RECEIVED, _S.CLASSIFIED, _S.PREVIEWING, _S.APPROVED, _S.COMMITTING, _S.DRIFTED}
)
_VERBS = {"delete": "deleted", "update": "updated", "insert": "inserted"}


@dataclass(frozen=True)
class AgentView:
    """One action, as `visible_actions(owner, is_admin=False)` returns it. No SQL, no intent."""

    id: uuid.UUID
    status: str
    decision: str | None
    policy_reason: str | None
    reviewer_note: str | None
    decided_by_name: str | None
    failure_reason: str | None
    created_at: datetime
    statement_type: str | None
    tables: list[str]
    drifts: int  # `drifted` events
    ai_unavailable: bool  # from the latest `decided` event
    committed_rows: int | None  # `committed` event data.rows; None for the stub
    impact: dict[str, Any] | None  # table_changes and cascade_tables


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str | None


def failure_message(reason: str | None, tables: list[str]) -> str:
    """The message for a failed action, rebuilt from what was stored.

    A query error's Postgres text is never stored (D-104), so it comes back as the `failed` code.
    """
    reason = reason or INTERNAL_ERROR
    if reason == "shadow_busy":
        return agent_messages.busy(BUSY_RETRY_SECONDS)
    if reason == "table_not_found" and tables:
        return agent_messages.table_not_found(tables[0])
    return agent_messages.failed(reason)


def _target(view: AgentView) -> tuple[str | None, dict[str, int]]:
    """The table the statement wrote to, and its counts. A cascade table is never the target."""
    changes: dict[str, Any] = (view.impact or {}).get("table_changes", {})
    cascades = set((view.impact or {}).get("cascade_tables", []))
    for name in view.tables:
        if name in changes and name not in cascades:
            return name, changes[name]
    for name, counts in changes.items():
        if name not in cascades:
            return name, counts
    return (view.tables[0] if view.tables else None), {}


def _committed(view: AgentView) -> str:
    table, counts = _target(view)
    verb = _VERBS.get(view.statement_type or "", "changed")
    rows = view.committed_rows
    if rows is None:  # the stub reports no count: fall back to the computed preview
        keys = (verb,) if verb in _VERBS.values() else tuple(_VERBS.values())  # MERGE: all three
        rows = sum(counts.get(key, 0) for key in keys)
    return agent_messages.committed(rows, verb, table or "the company database")


def over_budget(view: AgentView, *, now: datetime, settings: Settings) -> bool:
    """A review that has made the agent wait the whole polling budget (timed from `created_at`)."""
    return (
        view.status == _S.AWAITING_REVIEW.value
        and (now - view.created_at).total_seconds() >= settings.agent_poll_budget_minutes * 60
    )


def _waiting(view: AgentView, *, now: datetime, settings: Settings, polling: bool) -> str:
    if polling and over_budget(view, now=now, settings=settings):
        minutes = int((now - view.created_at).total_seconds() // 60)
        return agent_messages.still_waiting(str(view.id), minutes)
    if view.drifts > 0:
        return f"{agent_messages.drifted(str(view.id))} {agent_messages.KEEP_POLLING}"
    if view.ai_unavailable:
        return f"{agent_messages.ai_unavailable(str(view.id))} {agent_messages.KEEP_POLLING}"
    return agent_messages.pending(str(view.id))


def outcome_for(view: AgentView, *, now: datetime, settings: Settings, polling: bool) -> Outcome:
    status = ActionStatus(view.status)
    action_id = str(view.id)
    message: str | None
    if status == _S.COMMITTED:
        message = _committed(view)
    elif status == _S.BLOCKED:
        message = agent_messages.blocked(view.policy_reason or "blocked by policy")
    elif status == _S.AWAITING_REVIEW:
        message = _waiting(view, now=now, settings=settings, polling=polling)
    elif status == _S.REJECTED:
        message = agent_messages.rejected(
            view.decided_by_name or "a reviewer", view.reviewer_note or "no reason given", action_id
        )
    elif status == _S.EXPIRED:
        message = agent_messages.expired(settings.review_timeout_hours)
    elif status == _S.FAILED:
        message = failure_message(view.failure_reason, view.tables)
    elif status in _IN_PROGRESS:
        message = agent_messages.in_progress(action_id)
    else:  # executed: the rows are the answer
        message = None
    return Outcome(view.status, message)
