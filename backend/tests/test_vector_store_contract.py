"""TEST-12: one suite every VectorStore must pass.

Runs against MemoryVectorStore (the reference, no database) and PgVectorStore (scratch database).
A new store (Qdrant, after the MVP) is added to `STORES` and must pass the same tests.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import pytest
from sqlalchemy import event, text

from app.db.models import EMBEDDING_DIMENSIONS
from app.db.session import make_engine, make_sessionmaker
from app.services.vectorstores.base import VectorItem, VectorStore
from app.services.vectorstores.memory import MemoryVectorStore
from app.services.vectorstores.pgvector import PgVectorStore
from tests.conftest import AuthDb, asyncpg_url

DIM = EMBEDDING_DIMENSIONS


def vec(*leading: float) -> list[float]:
    """A vector that starts with these numbers and is zero after them."""
    return [*leading, *([0.0] * (DIM - len(leading)))]


@dataclass
class Harness:
    store: VectorStore
    # Creates the chunk (PgVectorStore needs a `knowledge_chunks` row) and returns its item.
    make_item: Callable[[tuple[str, ...], list[float]], Awaitable[VectorItem]]
    count: Callable[[], Awaitable[int]]


async def memory_harness(auth_db: AuthDb | None) -> AsyncIterator[Harness]:
    store = MemoryVectorStore(DIM)

    async def make_item(tables: tuple[str, ...], vector: list[float]) -> VectorItem:
        return VectorItem(uuid.uuid4(), vector, "doc.md", tables)

    async def count() -> int:
        return len(store._items)

    yield Harness(store, make_item, count)


async def pgvector_harness(auth_db: AuthDb | None) -> AsyncIterator[Harness]:
    assert auth_db is not None
    engine = make_engine(asyncpg_url(auth_db.app_url))
    sessionmaker = make_sessionmaker(engine)
    auth_db.execute("DELETE FROM knowledge_chunks")

    async def make_item(tables: tuple[str, ...], vector: list[float]) -> VectorItem:
        [(chunk_id,)] = auth_db.execute(
            "INSERT INTO knowledge_chunks (source, table_names, content)"
            " VALUES ('doc.md', %s, 'text') RETURNING id",
            (list(tables),),
        )
        return VectorItem(chunk_id, vector, "doc.md", tables)

    async def count() -> int:
        async with sessionmaker() as session:
            return (
                await session.execute(text("SELECT count(*) FROM chunk_embeddings"))
            ).scalar_one()

    try:
        yield Harness(PgVectorStore(sessionmaker), make_item, count)
    finally:
        auth_db.execute("DELETE FROM knowledge_chunks")
        await engine.dispose()


STORES = [
    pytest.param(memory_harness, id="memory"),
    pytest.param(
        pgvector_harness,
        id="pgvector",
        marks=pytest.mark.integration,
    ),
]


@pytest.fixture(params=STORES)
def store_kind(request: pytest.FixtureRequest) -> tuple[Callable, AuthDb | None]:
    """The harness factory, and the scratch database when the store needs one.

    A plain (not async) fixture: starting the database runs asyncio.run, which cannot happen
    inside the event loop.
    """
    factory = request.param
    auth_db = request.getfixturevalue("auth_db") if factory is pgvector_harness else None
    return factory, auth_db


@pytest.fixture
async def h(store_kind: tuple[Callable, AuthDb | None]) -> AsyncIterator[Harness]:
    factory, auth_db = store_kind
    async for harness in factory(auth_db):
        yield harness


async def test_results_come_back_in_order_of_similarity(h: Harness) -> None:
    far = await h.make_item((), vec(0.0, 1.0))
    near = await h.make_item((), vec(1.0, 0.1))
    mid = await h.make_item((), vec(1.0, 1.0))
    await h.store.upsert([far, near, mid])
    hits = await h.store.search(vec(1.0), k=3)
    assert [x.chunk_id for x in hits] == [near.chunk_id, mid.chunk_id, far.chunk_id]


async def test_scores_are_cosine_similarity_and_fall(h: Harness) -> None:
    same = await h.make_item((), vec(2.0))
    ortho = await h.make_item((), vec(0.0, 3.0))
    await h.store.upsert([same, ortho])
    hits = await h.store.search(vec(5.0), k=2)
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert hits[1].score == pytest.approx(0.0, abs=1e-5)
    assert [x.score for x in hits] == sorted((x.score for x in hits), reverse=True)


async def test_k_is_respected(h: Harness) -> None:
    await h.store.upsert([await h.make_item((), vec(1.0, i * 0.1)) for i in range(8)])
    assert len(await h.store.search(vec(1.0), k=3)) == 3
    assert len(await h.store.search(vec(1.0), k=20)) == 8


async def test_the_table_filter_matches_any_tag_and_leaves_out_untagged_chunks(h: Harness) -> None:
    users = await h.make_item(("users",), vec(1.0, 0.0))
    both = await h.make_item(("orders", "users"), vec(1.0, 0.1))
    orders = await h.make_item(("orders",), vec(1.0, 0.2))
    untagged = await h.make_item((), vec(1.0, 0.0))
    await h.store.upsert([users, both, orders, untagged])
    got = {x.chunk_id for x in await h.store.search(vec(1.0), k=10, tables=["users", "invoices"])}
    assert got == {users.chunk_id, both.chunk_id}
    everything = {x.chunk_id for x in await h.store.search(vec(1.0), k=10)}
    assert untagged.chunk_id in everything


async def test_a_filtered_search_still_returns_k_when_matches_exist(h: Harness) -> None:
    # 120 closer chunks do not match the filter; an index scan that stops after its first 40
    # candidates would find none of the 8 that do (pgvector iterative_scan).
    await h.store.upsert([await h.make_item(("other",), vec(1.0, i * 0.001)) for i in range(120)])
    wanted = [await h.make_item(("users",), vec(0.2, 1.0, i * 0.01)) for i in range(8)]
    await h.store.upsert(wanted)
    hits = await h.store.search(vec(1.0), k=5, tables=["users"])
    assert len(hits) == 5
    assert {x.chunk_id for x in hits} <= {w.chunk_id for w in wanted}


async def test_upserting_the_same_chunk_replaces_its_vector(h: Harness) -> None:
    item = await h.make_item((), vec(1.0))
    await h.store.upsert([item])
    await h.store.upsert([VectorItem(item.chunk_id, vec(0.0, 1.0), item.source, item.table_names)])
    [hit] = await h.store.search(vec(0.0, 1.0), k=5)
    assert hit.chunk_id == item.chunk_id
    assert hit.score == pytest.approx(1.0, abs=1e-5)
    assert await h.count() == 1


async def test_delete_removes_vectors_and_ignores_unknown_ids(h: Harness) -> None:
    keep = await h.make_item((), vec(1.0, 0.0))
    drop = await h.make_item((), vec(1.0, 0.1))
    await h.store.upsert([keep, drop])
    await h.store.delete([drop.chunk_id, uuid.uuid4()])
    await h.store.delete([])
    assert [x.chunk_id for x in await h.store.search(vec(1.0), k=5)] == [keep.chunk_id]


async def test_an_empty_store_finds_nothing(h: Harness) -> None:
    assert await h.store.search(vec(1.0), k=5) == []
    assert await h.store.search(vec(1.0), k=5, tables=["users"]) == []
    await h.store.upsert([])
    await h.store.delete([])


async def test_the_wrong_dimension_raises_and_stores_nothing(h: Harness) -> None:
    good = await h.make_item((), vec(1.0))
    bad = await h.make_item((), [1.0, 2.0, 3.0])
    with pytest.raises(Exception):  # noqa: B017 - each store raises its own error type
        await h.store.upsert([good, bad])
    assert await h.count() == 0
    with pytest.raises(Exception):  # noqa: B017
        await h.store.search([1.0, 2.0, 3.0], k=1)


async def test_clear_removes_everything(h: Harness) -> None:
    await h.store.upsert([await h.make_item((), vec(1.0, i * 0.1)) for i in range(3)])
    await h.store.clear()
    assert await h.count() == 0
    assert await h.store.search(vec(1.0), k=5) == []


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
async def test_pgvector_filtered_search_turns_on_iterative_scan(auth_db: AuthDb) -> None:
    """On a table this small the planner ignores the HNSW index, so a result check cannot see the
    setting; look at the SQL the store sends instead (pgvector docs: filtered HNSW scans)."""
    engine = make_engine(asyncpg_url(auth_db.app_url))
    seen: list[str] = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda *a: seen.append(a[2]))
    try:
        store = PgVectorStore(make_sessionmaker(engine))
        await store.search(vec(1.0), k=5, tables=["users"])
        assert any("hnsw.iterative_scan = strict_order" in sql for sql in seen)
        seen.clear()
        await store.search(vec(1.0), k=5)
        assert not any("iterative_scan" in sql for sql in seen)
    finally:
        await engine.dispose()
