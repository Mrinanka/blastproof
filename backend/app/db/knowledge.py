"""Queries on `knowledge_chunks` (backend plan step 11).

Knowledge is global: there is one main system and chunks carry no owner. Text and table tags live
here whichever vector store is used (ARCH-20).
"""

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, KnowledgeChunk


@dataclass(frozen=True)
class ChunkDraft:
    source: str
    table_names: tuple[str, ...]
    content: str


async def replace_source(
    session: AsyncSession, source: str, drafts: Sequence[ChunkDraft]
) -> tuple[list[uuid.UUID], list[KnowledgeChunk]]:
    """Delete the source's chunks (their pgvector rows cascade) and insert the new ones.

    Returns the old chunk IDs, so a store without cascade can drop its vectors, and the new rows.
    """
    # Two ingests of one source at once would otherwise both insert, leaving duplicate chunks.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:source))"), {"source": source}
    )
    old_ids = list(
        (await session.execute(select(KnowledgeChunk.id).where(KnowledgeChunk.source == source)))
        .scalars()
        .all()
    )
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source == source))
    chunks = [
        KnowledgeChunk(source=d.source, table_names=list(d.table_names), content=d.content)
        for d in drafts
    ]
    session.add_all(chunks)
    await session.flush()
    return old_ids, chunks


async def delete_source(session: AsyncSession, source: str) -> list[uuid.UUID]:
    """Delete one source's chunks; returns their IDs."""
    ids, _ = await replace_source(session, source, [])
    return ids


async def chunks_for_tables(session: AsyncSession, tables: Sequence[str]) -> list[KnowledgeChunk]:
    """Every chunk tagged with any of these tables, in a stable order."""
    if not tables:
        return []
    query = (
        select(KnowledgeChunk)
        .where(KnowledgeChunk.table_names.overlap(list(tables)))
        .order_by(KnowledgeChunk.source, KnowledgeChunk.created_at, KnowledgeChunk.id)
    )
    return list((await session.execute(query)).scalars().all())


async def chunks_by_ids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[KnowledgeChunk]:
    if not ids:
        return []
    query = select(KnowledgeChunk).where(KnowledgeChunk.id.in_(list(ids)))
    return list((await session.execute(query)).scalars().all())


async def all_chunks(session: AsyncSession) -> list[KnowledgeChunk]:
    query = select(KnowledgeChunk).order_by(KnowledgeChunk.created_at, KnowledgeChunk.id)
    return list((await session.execute(query)).scalars().all())


async def notes_for_table(
    session: AsyncSession, table: str, *, exclude_source: str
) -> list[KnowledgeChunk]:
    """Chunks tagged with this table, leaving out one source (the schema scan repeats columns)."""
    query = (
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.table_names.contains([table]), KnowledgeChunk.source != exclude_source
        )
        .order_by(KnowledgeChunk.source, KnowledgeChunk.created_at, KnowledgeChunk.id)
    )
    return list((await session.execute(query)).scalars().all())


async def save_knowledge_used(
    session: AsyncSession, action_id: uuid.UUID, refs: Sequence[Any]
) -> None:
    """Record which chunks an action used (`refs` are dataclasses, stored as JSON)."""
    await session.execute(
        update(Action)
        .where(Action.id == action_id)
        .values(knowledge_used=[asdict(ref) for ref in refs])
    )
