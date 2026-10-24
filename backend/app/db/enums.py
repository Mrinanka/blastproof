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
