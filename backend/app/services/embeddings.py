"""Turns text into vectors (backend plan step 11).

`Embedder` is the interface; stores never embed (ARCH-20). `FastEmbedEmbedder` runs a local ONNX
model (BAAI/bge-small-en-v1.5, D-059). Building it loads nothing: the model is read (and, the first
time on a machine, downloaded) on the first `embed` call, once, under a lock, in a worker thread.
"""

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

import structlog
from fastembed import TextEmbedding

from app.config import Settings

logger = structlog.get_logger()


class EmbeddingError(Exception):
    pass


class Embedder(ABC):
    model_name: str
    dimensions: int

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector of `dimensions` floats per text, in order."""


def supported_dimensions(model_name: str) -> int:
    """The model's vector size from fastembed's static list: no download, no model load."""
    for model in TextEmbedding.list_supported_models():
        if model["model"] == model_name:
            return int(model["dim"])
    raise EmbeddingError(f"EMBEDDING_MODEL {model_name!r} is not a model fastembed supports")


class FastEmbedEmbedder(Embedder):
    def __init__(self, model_name: str, cache_dir: Path | None) -> None:
        self.model_name = model_name
        self.dimensions = supported_dimensions(model_name)
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """Load the model if it is not loaded yet. Safe to call from many threads at once."""
        with self._lock:
            if self._model is None:
                logger.info("embedding_model_loading", model=self.model_name)
                cache = str(self._cache_dir) if self._cache_dir is not None else None
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=cache)
                logger.info("embedding_model_loaded", model=self.model_name)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        self.load()
        assert self._model is not None
        return [[float(x) for x in vector] for vector in self._model.embed(texts)]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, list(texts))

    async def warm_up(self) -> None:
        """Load the model in the background; a failure is logged and retried by the first use."""
        try:
            await asyncio.to_thread(self.load)
        except Exception as exc:
            logger.warning("embedding_warmup_failed", reason_code=type(exc).__name__)


def build_embedder(settings: Settings) -> FastEmbedEmbedder:
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
