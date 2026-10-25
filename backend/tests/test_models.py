"""BE-13, BE-15, BE-16 (shape only): the SQLAlchemy models match backend plan §5.

Unit tests, no database. Alembic does not compare CHECK constraints (its docs), so the enum
checks are guarded here; test_migrations.py runs `alembic check` for everything else.
"""

import re
import uuid

import pytest
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import CheckConstraint, Table

from app.config import Settings
from app.db.base import Base
from app.db.enums import (
    ActionStatus,
    AuditEventType,
    ConnectionProfile,
    Decision,
    HookDecision,
    PreviewChange,
    RiskLevel,
    SystemKind,
    UserRole,
)
from app.db.models import EMBEDDING_DIMENSIONS
from app.db.urls import (
    MissingMigratorUrlError,
    WrongMigratorRoleError,
    check_migrator_role,
    migrator_url,
)

# Backend plan §5, plus the columns step 4 added (D-084): actions.pinned_values,
# actions.failure_reason, preview_rows.created_at, before_images.created_at.
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "users": {
        "id", "email", "display_name", "password_hash", "role", "is_active", "token_version",
        "created_at",
    },
    "login_attempts": {"id", "email", "ip", "succeeded", "created_at"},
    "agents": {"id", "owner_id", "name", "api_key_hash", "key_prefix", "is_active", "created_at"},
    "systems": {
        "id", "name", "kind", "connection_profile", "neon_project_id", "neon_parent_branch_id",
    },
    "actions": {
        "id", "user_id", "agent_id", "system_id", "session_id", "retry_of", "tool", "intent",
        "sql", "pinned_sql", "sql_display", "pinned_values", "statement_type", "tables", "status",
        "decision", "risk_level", "policy_reason", "reviewer_note", "failure_reason",
        "matched_rules", "judge", "impact", "cascade_expectations", "impact_report",
        "knowledge_used", "preview_ms", "total_ms", "llm_input_tokens", "llm_output_tokens",
        "llm_cost_usd", "decided_by", "created_at", "decided_at", "expires_at",
    },
    "audit_events": {"id", "action_id", "event_type", "data", "created_at"},
    "preview_rows": {
        "action_id", "table_name", "primary_key", "before_hash", "change", "created_at",
    },
    "before_images": {
        "id", "action_id", "table_name", "primary_key", "row_before", "row_after", "created_at",
    },
    "hook_events": {
        "id", "user_id", "agent_id", "tool", "summary", "decision", "reason", "rule", "created_at",
    },
    "knowledge_chunks": {"id", "source", "table_names", "content", "created_at"},
    "chunk_embeddings": {"chunk_id", "embedding"},
}  # fmt: skip

# The canonical state machine in backend plan §5. "pending" is only an MCP reply word (BE-16).
PLAN_STATUSES = {
    "received", "classified", "previewing", "awaiting_review", "approved", "rejected", "expired",
    "blocked", "committing", "committed", "drifted", "executed", "failed",
}  # fmt: skip

# (table, column) -> the enum its CHECK constraint must allow, exactly.
ENUM_COLUMNS = {
    ("users", "role"): UserRole,
    ("systems", "kind"): SystemKind,
    ("systems", "connection_profile"): ConnectionProfile,
    ("actions", "status"): ActionStatus,
    ("actions", "decision"): Decision,
    ("actions", "risk_level"): RiskLevel,
    ("audit_events", "event_type"): AuditEventType,
    ("preview_rows", "change"): PreviewChange,
    ("hook_events", "decision"): HookDecision,
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def check_values(tbl: Table, column: str) -> set[str]:
    """The quoted values in the one CHECK constraint on this column."""
    checks = [
        c
        for c in tbl.constraints
        if isinstance(c, CheckConstraint) and str(c.sqltext).startswith(f"{column} IN")
    ]
    assert len(checks) == 1, f"{tbl.name}.{column} needs exactly one IN (...) check"
    return set(re.findall(r"'([^']*)'", str(checks[0].sqltext)))


def test_exactly_the_eleven_tables() -> None:
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)


@pytest.mark.parametrize("name", sorted(EXPECTED_COLUMNS))
def test_table_has_the_planned_columns(name: str) -> None:
    assert set(table(name).columns.keys()) == EXPECTED_COLUMNS[name]


@pytest.mark.parametrize(("location", "enum"), list(ENUM_COLUMNS.items()), ids=str)
def test_enum_columns_are_checked_against_their_enum(location: tuple[str, str], enum: type) -> None:
    tbl_name, column = location
    assert check_values(table(tbl_name), column) == {member.value for member in enum}


def test_every_check_constraint_is_named() -> None:
    # Alembic matches CHECKs by name only; an unnamed one could not be changed by a later migration.
    for tbl in Base.metadata.tables.values():
        for c in tbl.constraints:
            if isinstance(c, CheckConstraint):
                assert c.name, f"unnamed CHECK on {tbl.name}"


def test_action_statuses_are_the_plan_state_machine() -> None:
    assert {s.value for s in ActionStatus} == PLAN_STATUSES


def test_audit_event_types_cover_every_status_plus_the_extras() -> None:
    # D-037: one event per status change, named after the new status, plus three extra events.
    assert {e.value for e in AuditEventType} == PLAN_STATUSES | {
        "shadow_done",
        "decided",
        "fail_closed",
    }


def test_ids_are_uuids_and_timestamps_have_time_zones() -> None:
    # CONV-06: UUID ids; every timestamp is stored with its time zone.
    for tbl in Base.metadata.tables.values():
        for col in tbl.columns:
            if col.name == "id":
                assert col.type.python_type is uuid.UUID, f"{tbl.name}.id"
            if col.name.endswith("_at"):
                assert getattr(col.type, "timezone", False), f"{tbl.name}.{col.name}"


def test_embedding_is_a_384_dimension_vector_with_a_cosine_hnsw_index() -> None:
    # D-059 fixes the embedder (bge-small-en-v1.5, 384 dimensions); BE-15 asks for HNSW.
    col = table("chunk_embeddings").columns["embedding"]
    assert isinstance(col.type, VECTOR)
    assert col.type.dim == EMBEDDING_DIMENSIONS == 384
    indexes = list(table("chunk_embeddings").indexes)
    assert len(indexes) == 1
    options = indexes[0].dialect_options["postgresql"]
    assert options["using"] == "hnsw"
    assert options["ops"] == {"embedding": "vector_cosine_ops"}


def test_knowledge_tags_have_a_gin_index() -> None:
    indexes = {i.name: i for i in table("knowledge_chunks").indexes}
    index = indexes["ix_knowledge_chunks_table_names"]
    assert index.dialect_options["postgresql"]["using"] == "gin"
    assert [c.name for c in index.columns] == ["table_names"]


def test_only_chunk_embeddings_cascade() -> None:
    # Actions and their audit trail are never deleted through a cascade.
    cascading = {
        (tbl.name, fk.parent.name, fk.ondelete)
        for tbl in Base.metadata.tables.values()
        for fk in tbl.foreign_keys
        if fk.ondelete
    }
    assert cascading == {("chunk_embeddings", "chunk_id", "CASCADE")}


def test_preview_rows_key_is_one_row_per_affected_row() -> None:
    pk = [c.name for c in table("preview_rows").primary_key.columns]
    assert pk == ["action_id", "table_name", "primary_key"]


def test_users_email_is_unique_ignoring_case() -> None:
    unique = [i for i in table("users").indexes if i.unique]
    assert len(unique) == 1
    assert str(unique[0].expressions[0]) == "lower(email)"


# --- the migrator URL used by migrations/env.py --------------------------------------------------


def test_migrator_url_prefers_the_explicit_one() -> None:
    settings = Settings(_env_file=None, blastproof_migrator_url="postgresql://from-settings/x")
    assert migrator_url("postgresql://explicit/x", settings) == "postgresql://explicit/x"
    assert migrator_url(None, settings) == "postgresql://from-settings/x"


def test_missing_migrator_url_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLASTPROOF_MIGRATOR_URL", raising=False)
    with pytest.raises(MissingMigratorUrlError, match="BLASTPROOF_MIGRATOR_URL"):
        migrator_url(None, Settings(_env_file=None))


def test_only_the_owner_role_may_migrate() -> None:
    check_migrator_role("blastproof_owner")
    for role in ("blastproof_app", "blastproof", "postgres"):
        with pytest.raises(
            WrongMigratorRoleError, match=f"must run as blastproof_owner, not {role}"
        ):
            check_migrator_role(role)


def test_empty_migrator_url_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example-style placeholder lines leave the variable empty until it is filled in.
    monkeypatch.setenv("BLASTPROOF_MIGRATOR_URL", "")
    with pytest.raises(MissingMigratorUrlError, match="BLASTPROOF_MIGRATOR_URL"):
        migrator_url(None, Settings(_env_file=None))
