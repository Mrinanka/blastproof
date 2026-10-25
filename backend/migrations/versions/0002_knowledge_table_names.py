"""knowledge_chunks.table_names: a chunk can be about several tables (step 11).

Replaces the single `table_name` with `table_names text[]` and a GIN index, so retrieval can ask for
"any chunk that mentions one of these tables" with `table_names && :tables`. Existing rows keep
their one tag. Grants are table-level and stay as they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "table_names",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE knowledge_chunks SET table_names = ARRAY[table_name] WHERE table_name IS NOT NULL"
    )
    op.drop_index("ix_knowledge_chunks_table_name", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "table_name")
    op.create_index(
        "ix_knowledge_chunks_table_names",
        "knowledge_chunks",
        ["table_names"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("table_name", sa.Text(), nullable=True))
    op.execute("UPDATE knowledge_chunks SET table_name = table_names[1]")
    op.drop_index("ix_knowledge_chunks_table_names", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "table_names")
    op.create_index("ix_knowledge_chunks_table_name", "knowledge_chunks", ["table_name"])
