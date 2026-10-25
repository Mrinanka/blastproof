"""Vector stores behind one interface (ARCH-20)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.vectorstores.base import SearchHit, VectorItem, VectorStore
from app.services.vectorstores.pgvector import PgVectorStore

__all__ = ["SearchHit", "VectorItem", "VectorStore", "build_vector_store"]


def build_vector_store(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession] | None
) -> VectorStore | None:
    """The configured store, or None when there is no Blastproof database (offline startup)."""
    if sessionmaker is None:
        return None
    # Settings accepts only "pgvector" until QdrantStore exists (after the MVP).
    return PgVectorStore(sessionmaker)
