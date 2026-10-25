"""The vector store interface (ARCH-20). Stores hold vectors only; text lives in Postgres."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class VectorItem:
    chunk_id: UUID
    vector: list[float]
    # Metadata for stores that keep their own (Qdrant); Postgres stays the source of truth.
    source: str
    table_names: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    chunk_id: UUID
    score: float  # cosine similarity: 1 - cosine distance, higher is closer


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, items: Sequence[VectorItem]) -> None:
        """Add the vectors, replacing any that exist for the same chunk ID."""

    @abstractmethod
    async def search(
        self, vector: list[float], k: int, tables: Sequence[str] | None = None
    ) -> list[SearchHit]:
        """The `k` closest chunks, best first. With `tables`, only chunks tagged with any."""

    @abstractmethod
    async def delete(self, chunk_ids: Sequence[UUID]) -> None:
        """Remove these vectors. Unknown IDs are ignored."""

    @abstractmethod
    async def clear(self) -> None:
        """Remove every vector (used by reindex)."""
