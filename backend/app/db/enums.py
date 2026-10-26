"""The fixed value sets stored in Blastproof's tables. Each is enforced by a CHECK constraint."""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class SystemKind(StrEnum):
    POSTGRES = "postgres"


class ConnectionProfile(StrEnum):
    """Which credentials and NeonClient a system uses (D-027)."""

    NEON = "neon"
    LOCAL = "local"


class ActionStatus(StrEnum):
    """The canonical state machine (backend plan §5). "pending" is never stored (BE-16)."""

    RECEIVED = "received"
    CLASSIFIED = "classified"
    PREVIEWING = "previewing"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    COMMITTING = "committing"
    COMMITTED = "committed"
    DRIFTED = "drifted"
    EXECUTED = "executed"
    FAILED = "failed"


_S = ActionStatus
# Every legal status change (backend plan §5). `received -> executed | failed` is a read, which
# never enters the graph. `expired`, `drifted` and the others come from steps 15 and 16.
ALLOWED_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    _S.RECEIVED: frozenset({_S.CLASSIFIED, _S.EXECUTED, _S.FAILED}),
    _S.CLASSIFIED: frozenset({_S.PREVIEWING, _S.APPROVED, _S.BLOCKED, _S.FAILED}),
    _S.PREVIEWING: frozenset({_S.AWAITING_REVIEW, _S.APPROVED, _S.BLOCKED, _S.FAILED}),
    _S.AWAITING_REVIEW: frozenset({_S.APPROVED, _S.REJECTED, _S.EXPIRED, _S.FAILED}),
    _S.APPROVED: frozenset({_S.COMMITTING, _S.FAILED}),
    _S.COMMITTING: frozenset({_S.COMMITTED, _S.DRIFTED, _S.FAILED}),
    _S.DRIFTED: frozenset({_S.PREVIEWING, _S.FAILED}),
    _S.REJECTED: frozenset(),
    _S.EXPIRED: frozenset(),
    _S.BLOCKED: frozenset(),
    _S.COMMITTED: frozenset(),
    _S.EXECUTED: frozenset(),
    _S.FAILED: frozenset(),
}


class Decision(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PreviewChange(StrEnum):
    DELETED = "deleted"
    UPDATED = "updated"
    INSERTED = "inserted"


class HookDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class AuditEventType(StrEnum):
    """D-037: one event per status change, named after the new status, plus three extras.

    tests/test_models.py checks this stays equal to ActionStatus plus the extras.
    """

    RECEIVED = "received"
    CLASSIFIED = "classified"
    PREVIEWING = "previewing"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    COMMITTING = "committing"
    COMMITTED = "committed"
    DRIFTED = "drifted"
    EXECUTED = "executed"
    FAILED = "failed"
    SHADOW_DONE = "shadow_done"
    DECIDED = "decided"
    FAIL_CLOSED = "fail_closed"
