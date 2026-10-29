"""Step 11: the embedder loads lazily, once, off the event loop (BE-61, ARCH-20, TEST-07)."""

import asyncio
import threading
import time
from pathlib import Path

import pytest

from app.config import DEFAULT_EMBEDDING_CACHE_DIR, Settings
from app.main import create_app
from app.services import embeddings
from app.services.embeddings import EmbeddingError, FastEmbedEmbedder, build_embedder

MODEL = "BAAI/bge-small-en-v1.5"


class StubTextEmbedding:
    """A fastembed TextEmbedding stand-in: counts loads and threads, makes 384 floats."""

    constructed = 0
    embed_threads: list[int] = []
    dims = {MODEL: 384, "x/big": 768}

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        time.sleep(0.05)  # widen the race between concurrent first calls
        type(self).constructed += 1
        self.model_name = model_name
        self.cache_dir = cache_dir

    @classmethod
    def list_supported_models(cls) -> list[dict]:
        return [{"model": name, "dim": dim} for name, dim in cls.dims.items()]

    def embed(self, texts: list[str]):  # type: ignore[no-untyped-def]
        type(self).embed_threads.append(threading.get_ident())
        for text in texts:
            yield [float(len(text))] * self.dims[self.model_name]


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch, no_network: object) -> type[StubTextEmbedding]:
    StubTextEmbedding.constructed = 0
    StubTextEmbedding.embed_threads = []
    monkeypatch.setattr(embeddings, "TextEmbedding", StubTextEmbedding)
    return StubTextEmbedding


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)  # type: ignore[arg-type]


def test_building_the_embedder_loads_no_model(stub_model: type[StubTextEmbedding]) -> None:
    embedder = build_embedder(settings())
    assert embedder.dimensions == 384
    assert stub_model.constructed == 0


def test_creating_the_app_loads_no_model(stub_model: type[StubTextEmbedding]) -> None:
    create_app(settings())
    assert stub_model.constructed == 0


def test_a_768_dimension_model_stops_startup(stub_model: type[StubTextEmbedding]) -> None:
    with pytest.raises(EmbeddingError, match="768"):
        create_app(settings(embedding_model="x/big"))
    assert stub_model.constructed == 0


def test_an_unknown_model_stops_startup() -> None:
    with pytest.raises(EmbeddingError, match="not a model fastembed supports"):
        create_app(settings(embedding_model="nobody/nothing"))


async def test_ten_concurrent_first_calls_load_the_model_once(
    stub_model: type[StubTextEmbedding],
) -> None:
    embedder = FastEmbedEmbedder(MODEL, None)
    results = await asyncio.gather(*(embedder.embed([f"text {i}"]) for i in range(10)))
    assert stub_model.constructed == 1
    assert all(len(r) == 1 and len(r[0]) == 384 for r in results)


async def test_embedding_runs_off_the_event_loop(stub_model: type[StubTextEmbedding]) -> None:
    embedder = FastEmbedEmbedder(MODEL, None)
    vectors = await embedder.embed(["a", "bb"])
    assert [v[0] for v in vectors] == [1.0, 2.0]
    assert stub_model.embed_threads
    assert threading.get_ident() not in stub_model.embed_threads


async def test_no_texts_do_not_load_the_model(stub_model: type[StubTextEmbedding]) -> None:
    assert await FastEmbedEmbedder(MODEL, None).embed([]) == []
    assert stub_model.constructed == 0


async def test_the_cache_folder_is_passed_to_the_model(tmp_path: Path) -> None:
    embedder = FastEmbedEmbedder(MODEL, tmp_path)
    await embedder.embed(["x"])
    assert embedder._model.cache_dir == str(tmp_path)  # type: ignore[union-attr]


async def test_a_failed_warm_up_is_only_logged_and_the_next_use_retries(
    monkeypatch: pytest.MonkeyPatch, stub_model: type[StubTextEmbedding]
) -> None:
    real_init = StubTextEmbedding.__init__
    attempts = []

    def flaky(self: StubTextEmbedding, *args: object, **kwargs: object) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("offline")
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(StubTextEmbedding, "__init__", flaky)
    embedder = FastEmbedEmbedder(MODEL, None)
    await embedder.warm_up()  # does not raise
    assert len(await embedder.embed(["x"])) == 1
    assert len(attempts) == 2


def test_the_default_cache_folder_is_inside_the_backend_and_ignored_by_git() -> None:
    assert settings().embedding_cache_dir == DEFAULT_EMBEDDING_CACHE_DIR
    assert DEFAULT_EMBEDDING_CACHE_DIR.parts[-3:] == ("backend", ".cache", "fastembed")


def test_empty_values_mean_the_defaults() -> None:
    s = settings(embedding_model="", embedding_cache_dir="", vector_store="")
    assert (s.embedding_model, s.vector_store) == (MODEL, "pgvector")
    assert s.embedding_cache_dir == DEFAULT_EMBEDDING_CACHE_DIR


def test_only_the_pgvector_store_is_accepted() -> None:
    with pytest.raises(ValueError):
        settings(vector_store="qdrant")


async def test_ingest_embeds_in_small_batches_and_keeps_order() -> None:
    """F10 hand test (D-280): 89 chunks in batches of 64 got the 1 GB Fly machine OOM-killed.

    Memory grows with the batch, so every model call gets at most 8 texts, in order.
    """
    from app.services.knowledge import EMBED_BATCH_SIZE, KnowledgeService
    from tests.knowledge_helpers import WordEmbedder

    class CountingEmbedder(WordEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.sizes: list[int] = []

        async def embed(self, texts):  # type: ignore[no-untyped-def]
            self.sizes.append(len(texts))
            return await super().embed(texts)

    embedder = CountingEmbedder()
    service = KnowledgeService(None, embedder, None)  # type: ignore[arg-type]
    texts = [f"chunk {i}" for i in range(89)]
    vectors = await service._embed_all(texts)

    assert EMBED_BATCH_SIZE == 8
    assert max(embedder.sizes) <= 8
    assert sum(embedder.sizes) == 89
    assert vectors == [WordEmbedder.vector(t) for t in texts]
