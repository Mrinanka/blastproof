"""SQLAlchemy models for Blastproof's eleven tables (backend plan §5, BE-13).

Columns follow §5, plus four step 4 added (D-084): `actions.pinned_values`,
`actions.failure_reason`, and `created_at` on `preview_rows` and `before_images` (for retention).
Privileges and the append-only trigger on `audit_events` live in the migration, not here.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_check
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

# BAAI/bge-small-en-v1.5 (D-059). Changing the model means a migration and re-embedding.
EMBEDDING_DIMENSIONS = 384

_NEW_UUID = text("gen_random_uuid()")
_NOW = text("now()")


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=_NEW_UUID)


def _created_at() -> Mapped[datetime]:
    return mapped_column(server_default=_NOW)


class User(Base):
    """Dashboard logins. Other users only ever see `display_name`, never the email."""

    __tablename__ = "users"
    __table_args__ = (
        enum_check("role", UserRole),
        # One account per address, whatever its case.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str]
    display_name: Mapped[str]
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(server_default=UserRole.MEMBER.value)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    # Bumping it revokes every JWT issued to this user.
    token_version: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()


class LoginAttempt(Base):
    """Login rate limiting, per email and IP and per email alone (SEC-06)."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email_created_at", "email", "created_at"),
        Index("ix_login_attempts_email_ip_created_at", "email", "ip", "created_at"),
    )

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str]
    ip: Mapped[str] = mapped_column(INET)
    succeeded: Mapped[bool]
    created_at: Mapped[datetime] = _created_at()


class Agent(Base):
    """One per agent key, owned by one user. Only the key's SHA-256 hash is stored."""

    __tablename__ = "agents"
    __table_args__ = (Index("ix_agents_owner_id", "owner_id"),)

    id: Mapped[uuid.UUID] = _id()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    api_key_hash: Mapped[str] = mapped_column(unique=True)
    key_prefix: Mapped[str]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()


class System(Base):
    """A company database Blastproof protects. Credentials stay in env vars (D-027)."""

    __tablename__ = "systems"
    __table_args__ = (
        enum_check("kind", SystemKind),
        enum_check("connection_profile", ConnectionProfile),
    )

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(server_default=SystemKind.POSTGRES.value)
    connection_profile: Mapped[str]
    neon_project_id: Mapped[str | None]
    neon_parent_branch_id: Mapped[str | None]


class Action(Base):
    """One row per intercepted MCP action.

    `sql` is exactly what the agent sent and is never returned by the API. `pinned_sql` is what is
    previewed, approved and committed. `sql_display` (pinned, PII masked) is the only SQL that may
    leave the backend.
    """

    __tablename__ = "actions"
    __table_args__ = (
        enum_check("status", ActionStatus),
        enum_check("decision", Decision),
        enum_check("risk_level", RiskLevel),
        Index("ix_actions_user_id_created_at", "user_id", "created_at"),
        # The per-agent write rate limit counts recent rows (D-032).
        Index("ix_actions_agent_id_created_at", "agent_id", "created_at"),
        Index("ix_actions_status", "status"),
        Index("ix_actions_retry_of", "retry_of"),
    )

    id: Mapped[uuid.UUID] = _id()
    # The agent owner's id: the only thing ownership checks look at.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("systems.id"))
    session_id: Mapped[str | None]
    retry_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("actions.id"))
    tool: Mapped[str]
    intent: Mapped[str | None]
    sql: Mapped[str | None]
    pinned_sql: Mapped[str | None]
    sql_display: Mapped[str | None]
    # e.g. {"now()": "2026-10-24T12:00:00Z"}; the intercept time cannot be recomputed later.
    pinned_values: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    statement_type: Mapped[str | None]
    tables: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str]
    decision: Mapped[str | None]
    risk_level: Mapped[str | None]
    policy_reason: Mapped[str | None]  # from rules
    reviewer_note: Mapped[str | None]  # the human's reject reason, sent to the agent
    failure_reason: Mapped[str | None]  # the reason code in the `failed` message
    matched_rules: Mapped[Any] = mapped_column(JSONB, nullable=True)
    judge: Mapped[Any] = mapped_column(JSONB, nullable=True)
    impact: Mapped[Any] = mapped_column(JSONB, nullable=True)
    cascade_expectations: Mapped[Any] = mapped_column(JSONB, nullable=True)
    impact_report: Mapped[Any] = mapped_column(JSONB, nullable=True)
    knowledge_used: Mapped[Any] = mapped_column(JSONB, nullable=True)
    preview_ms: Mapped[int | None]
    total_ms: Mapped[int | None]
    llm_input_tokens: Mapped[int | None]
    llm_output_tokens: Mapped[int | None]
    llm_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    decided_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]


class AuditEvent(Base):
    """Append-only timeline. The app may only SELECT and INSERT; a trigger stops everyone else."""

    __tablename__ = "audit_events"
    __table_args__ = (
        enum_check("event_type", AuditEventType),
        Index("ix_audit_events_action_id_created_at", "action_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _id()
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actions.id"))
    event_type: Mapped[str]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()


class PreviewRow(Base):
    """One row per row the shadow run changed; the commit's drift check compares against these."""

    __tablename__ = "preview_rows"
    __table_args__ = (enum_check("change", PreviewChange),)

    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actions.id"), primary_key=True)
    table_name: Mapped[str] = mapped_column(primary_key=True)
    primary_key: Mapped[Any] = mapped_column(JSONB, primary_key=True)
    before_hash: Mapped[str | None]  # null for inserted rows
    change: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class BeforeImage(Base):
    """Rows as they were before a commit, saved for undo later. Never leaves the backend."""

    __tablename__ = "before_images"
    __table_args__ = (Index("ix_before_images_action_id", "action_id"),)

    id: Mapped[uuid.UUID] = _id()
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actions.id"))
    table_name: Mapped[str]
    primary_key: Mapped[Any] = mapped_column(JSONB)
    row_before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    row_after: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class HookEvent(Base):
    """One per Claude Code hook call; kept out of `actions` so Bash noise never floods the queue."""

    __tablename__ = "hook_events"
    __table_args__ = (
        enum_check("decision", HookDecision),
        Index("ix_hook_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    tool: Mapped[str]
    summary: Mapped[str]  # masked
    decision: Mapped[str]
    reason: Mapped[str]
    rule: Mapped[str | None]
    created_at: Mapped[datetime] = _created_at()


class KnowledgeChunk(Base):
    """Knowledge text and metadata live here, whichever vector store is used (ARCH-20)."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_source", "source"),
        # A chunk can be about several tables; retrieval asks for any overlap (`&&`).
        Index("ix_knowledge_chunks_table_names", "table_names", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _id()
    source: Mapped[str]
    table_names: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    content: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class ChunkEmbedding(Base):
    """Vectors for the pgvector store only; Qdrant keeps its own."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        # Cosine distance: correct whether or not the embedder normalises (search with `<=>`).
        Index(
            "ix_chunk_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[Any] = mapped_column(VECTOR(EMBEDDING_DIMENSIONS))
