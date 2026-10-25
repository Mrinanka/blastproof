"""Test doubles for step 11: an embedder that needs no model, and stores that fail on purpose."""

import hashlib
import math
import re
from collections.abc import Sequence
from uuid import UUID

from app.db.models import EMBEDDING_DIMENSIONS
from app.services.embeddings import Embedder
from app.services.vectorstores.base import SearchHit, VectorItem, VectorStore


class WordEmbedder(Embedder):
    """A bag of words hashed into 384 slots: texts that share words are closer. Deterministic."""

    model_name = "test/word-hash"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        out = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"[a-z0-9_]+", text.lower()):
            slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            out[slot % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


class BrokenEmbedder(Embedder):
    model_name = "test/broken"
    dimensions = EMBEDDING_DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("model exploded: secret@example.com")


class BrokenStore(VectorStore):
    async def upsert(self, items: Sequence[VectorItem]) -> None:
        raise RuntimeError("store down")

    async def search(
        self, vector: list[float], k: int, tables: Sequence[str] | None = None
    ) -> list[SearchHit]:
        raise RuntimeError("store down")

    async def delete(self, chunk_ids: Sequence[UUID]) -> None:
        raise RuntimeError("store down")

    async def clear(self) -> None:
        raise RuntimeError("store down")
