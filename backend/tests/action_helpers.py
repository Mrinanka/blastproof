"""Seeding for the actions API tests: actions inserted with SQL as `blastproof_app`.

No graph exists yet (step 14), so a full action is built column by column from the real writers'
output (`Diff.impact_json()`, `JudgeResult.to_json()`, `asdict(KnowledgeRef)`).
"""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from app.core.security import create_access_token
from app.services.impact import ImpactReport, ImpactResult
from app.services.judge import JudgeResult, JudgeVerdict
from app.services.knowledge import KnowledgeRef
from app.services.shadow import Diff, SampleRow, TableDiff
from tests.conftest import TEST_JWT_SECRET, AuthDb, TestUser

JSON_COLUMNS = {
    "pinned_values",
    "matched_rules",
    "judge",
    "impact",
    "cascade_expectations",
    "impact_report",
    "knowledge_used",
}
RAW_SQL = "DELETE FROM users WHERE email = 'raw.secret@example.com' AND last_login_at < now()"
_LATER = "AND last_login_at < '2026-10-01T00:00:00Z'"
PINNED_SQL = f"DELETE FROM users WHERE email = 'raw.secret@example.com' {_LATER}"
DISPLAY_SQL = f"DELETE FROM users WHERE email = '[email]' {_LATER}"


def token_for(user: TestUser) -> dict[str, str]:
    """Authorization headers for `user` (version 0, as `make_user` creates it)."""
    token, _ = create_access_token(user.id, 0, TEST_JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


def make_system(db: AuthDb) -> uuid.UUID:
    name = f"company-{uuid.uuid4().hex[:8]}"
    [(system_id,)] = db.execute(
        "INSERT INTO systems (name, connection_profile) VALUES (%s, 'local') RETURNING id", (name,)
    )
    return system_id


def insert_action(
    db: AuthDb, user: TestUser, agent_id: uuid.UUID, system_id: uuid.UUID, **columns: Any
) -> uuid.UUID:
    """One action row owned by `user`; `columns` override the defaults of a plain executed read."""
    values: dict[str, Any] = {"tool": "query_database", "status": "executed", "tables": []}
    values |= columns
    values = {
        key: Jsonb(value) if key in JSON_COLUMNS and value is not None else value
        for key, value in values.items()
    }
    names = ["user_id", "agent_id", "system_id", *values]
    marks = ", ".join(["%s"] * len(names))
    [(action_id,)] = db.execute(
        f"INSERT INTO actions ({', '.join(names)}) VALUES ({marks}) RETURNING id",
        (user.id, agent_id, system_id, *values.values()),
    )
    return action_id


def insert_event(
    db: AuthDb, action_id: uuid.UUID, event_type: str, data: dict[str, Any] | None = None
) -> None:
    db.execute(
        "INSERT INTO audit_events (action_id, event_type, data) VALUES (%s, %s, %s)",
        (action_id, event_type, Jsonb(data or {})),
    )


def sample_diff(*, samples: int = 2) -> Diff:
    """A small delete with one cascade, one breakdown and one derived group."""
    return Diff(
        tables={
            "users": TableDiff("users", deleted=10, rows_before=100),
            "orders": TableDiff("orders", deleted=4, rows_before=50, is_cascade=True),
            "payments": TableDiff("payments", rows_before=20),
        },
        breakdowns={"users.plan": {"free": 8, "pro": 2}},
        derived_groups={"users.paying": 2},
        samples=tuple(
            SampleRow("users", "deleted", "before", {"id": i, "email": "[email]", "plan": "free"})
            for i in range(samples)
        ),
    )


def sample_report() -> ImpactResult:
    return ImpactResult(
        facts=None,  # type: ignore[arg-type]
        report=ImpactReport(
            headline="Deletes 10 users and 4 orders.",
            key_points=["2 of the users are on the pro plan."],
            concerns=["Paying customers are affected."],
        ),
        source="llm",
        fallback_reason=None,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.0012"),
        duration_ms=900,
    )


def sample_judge() -> JudgeResult:
    return JudgeResult(
        "llm",
        "google_genai:test",
        JudgeVerdict(
            recommendation="review", risk_level="high", confidence=0.9, reason="Paying users."
        ),
        None,
        False,
        100,
        20,
        Decimal("0.001"),
        700,
    )


def full_action_columns(**overrides: Any) -> dict[str, Any]:
    """Every column of a write that is waiting for review, from the real writers' output."""
    now = datetime.now(UTC)
    columns: dict[str, Any] = {
        "tool": "query_database",
        "status": "awaiting_review",
        "decision": "review",
        "risk_level": "high",
        "intent": "Clean up inactive users, ask jane.doe@example.com",
        "sql": RAW_SQL,
        "pinned_sql": PINNED_SQL,
        "sql_display": DISPLAY_SQL,
        "pinned_values": {"now()": "2026-10-01T00:00:00Z"},
        "statement_type": "DELETE",
        "tables": ["users", "orders"],
        "policy_reason": "Deletes paying customers.",
        "matched_rules": [
            {"name": "Delete many", "outcome": "review", "reason": "Large delete.", "pass": "post"}
        ],
        "judge": sample_judge().to_json(),
        "impact": sample_diff().impact_json(),
        "cascade_expectations": [],
        "impact_report": sample_report().report_json(),
        "knowledge_used": [
            asdict(
                KnowledgeRef(
                    "c1", "schema_docs.md", ["users"], "Users: mail x@y.com", "table", None
                )
            )
        ],
        "preview_ms": 12000,
        "total_ms": 15000,
        "llm_input_tokens": 120,
        "llm_output_tokens": 70,
        "llm_cost_usd": Decimal("0.002200"),
        "expires_at": now + timedelta(hours=24),
    }
    return columns | overrides
