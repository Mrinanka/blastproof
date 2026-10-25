"""An in-memory VectorStore: the reference implementation the contract tests compare against."""

import math
from collections.abc import Sequence
from uuid import UUID

from app.services.vectorstores.base import SearchHit, VectorItem, VectorStore


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


class MemoryVectorStore(VectorStore):
    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions
        self._items: dict[UUID, VectorItem] = {}

    def _check(self, vector: Sequence[float]) -> None:
        if len(vector) != self._dimensions:
            raise ValueError(f"expected {self._dimensions} dimensions, got {len(vector)}")

    async def upsert(self, items: Sequence[VectorItem]) -> None:
        for item in items:
            self._check(item.vector)
        for item in items:
            self._items[item.chunk_id] = item

    async def search(
        self, vector: list[float], k: int, tables: Sequence[str] | None = None
    ) -> list[SearchHit]:
        self._check(vector)
        wanted = set(tables) if tables is not None else None
        hits = [
            SearchHit(item.chunk_id, _cosine(vector, item.vector))
            for item in self._items.values()
            if wanted is None or wanted & set(item.table_names)
        ]
        hits.sort(key=lambda hit: (-hit.score, str(hit.chunk_id)))
        return hits[:k]

    async def delete(self, chunk_ids: Sequence[UUID]) -> None:
        for chunk_id in chunk_ids:
            self._items.pop(chunk_id, None)

    async def clear(self) -> None:
        self._items.clear()
