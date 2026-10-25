"""The default VectorStore: vectors in Postgres with pgvector (`chunk_embeddings`).

The HNSW index is built for cosine distance, so searches use `<=>` (D-085). The chunk's table tags
come from `knowledge_chunks` (Postgres is the source of truth), so the item's metadata is ignored.
"""

from collections.abc import Sequence
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import bindparam, delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import EMBEDDING_DIMENSIONS, ChunkEmbedding
from app.services.vectorstores.base import SearchHit, VectorItem, VectorStore

_SEARCH = text(
    "SELECT e.chunk_id, 1 - (e.embedding <=> :vector) AS score"
    " FROM chunk_embeddings e ORDER BY e.embedding <=> :vector LIMIT :k"
).bindparams(bindparam("vector", type_=VECTOR(EMBEDDING_DIMENSIONS)))

_SEARCH_TABLES = text(
    "SELECT e.chunk_id, 1 - (e.embedding <=> :vector) AS score"
    " FROM chunk_embeddings e JOIN knowledge_chunks c ON c.id = e.chunk_id"
    " WHERE c.table_names && :tables ORDER BY e.embedding <=> :vector LIMIT :k"
).bindparams(bindparam("vector", type_=VECTOR(EMBEDDING_DIMENSIONS)))


class PgVectorStore(VectorStore):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def upsert(self, items: Sequence[VectorItem]) -> None:
        if not items:
            return
        rows = [{"chunk_id": i.chunk_id, "embedding": i.vector} for i in items]
        statement = insert(ChunkEmbedding)
        statement = statement.on_conflict_do_update(
            index_elements=["chunk_id"], set_={"embedding": statement.excluded.embedding}
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement, rows)

    async def search(
        self, vector: list[float], k: int, tables: Sequence[str] | None = None
    ) -> list[SearchHit]:
        async with self._sessionmaker() as session, session.begin():
            if tables is None:
                result = await session.execute(_SEARCH, {"vector": vector, "k": k})
            else:
                # A filtered HNSW scan can stop short of k rows; strict_order keeps scanning
                # until it has them (pgvector 0.8.0+).
                await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
                result = await session.execute(
                    _SEARCH_TABLES, {"vector": vector, "k": k, "tables": list(tables)}
                )
            return [SearchHit(row.chunk_id, float(row.score)) for row in result]

    async def delete(self, chunk_ids: Sequence[UUID]) -> None:
        if not chunk_ids:
            return
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(list(chunk_ids)))
            )

    async def clear(self) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(ChunkEmbedding))
