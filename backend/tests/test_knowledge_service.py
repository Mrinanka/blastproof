"""Step 11: ingest, reindex, retrieve and knowledge_used (BE-59 to BE-63, ARCH-20)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import knowledge as knowledge_db
from app.db.actions import create_action
from app.db.session import make_engine, make_sessionmaker
from app.services import knowledge as knowledge_module
from app.services.embeddings import Embedder
from app.services.knowledge import KnowledgeService, RetrievalError
from app.services.vectorstores.memory import MemoryVectorStore
from app.services.vectorstores.pgvector import PgVectorStore
from tests.conftest import AuthDb, asyncpg_url
from tests.knowledge_helpers import BrokenEmbedder, BrokenStore, WordEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

USERS_DOC = b"""## table: users

Users are customers with a plan.

## table: orders

Orders belong to users.

## Business rules

Never delete a paying customer. Enterprise accounts must never be deleted.
"""


@dataclass
class Env:
    auth_db: AuthDb
    service: KnowledgeService
    store: PgVectorStore
    sessionmaker: async_sessionmaker[AsyncSession]

    def chunks(self, source: str | None = None) -> list[tuple]:
        where, args = ("WHERE source = %s", (source,)) if source else ("", ())
        return self.auth_db.execute(
            f"SELECT id, source, table_names FROM knowledge_chunks {where} ORDER BY content", args
        )

    def vector_count(self) -> int:
        [(n,)] = self.auth_db.execute("SELECT count(*) FROM chunk_embeddings")
        return n

    def service_with(self, embedder: Embedder, store: object) -> KnowledgeService:
        return KnowledgeService(self.sessionmaker, embedder, store)  # type: ignore[arg-type]


@pytest.fixture
async def env(auth_db: AuthDb) -> AsyncIterator[Env]:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    sessionmaker = make_sessionmaker(engine)
    auth_db.execute("DELETE FROM knowledge_chunks")
    store = PgVectorStore(sessionmaker)
    service = KnowledgeService(sessionmaker, WordEmbedder(), store)
    try:
        yield Env(auth_db, service, store, sessionmaker)
    finally:
        auth_db.execute("DELETE FROM knowledge_chunks")
        await engine.dispose()


# --- ingest, delete, reindex --------------------------------------------------------------------


async def test_ingest_stores_chunks_tags_and_vectors(env: Env) -> None:
    assert await env.service.ingest("rules.md", USERS_DOC) == 3
    rows = env.chunks("rules.md")
    assert sorted(tuple(r[2]) for r in rows) == [(), ("orders",), ("users",)]
    assert env.vector_count() == 3


async def test_ingesting_a_source_again_replaces_its_chunks_and_vectors(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    old_ids = {r[0] for r in env.chunks("rules.md")}
    assert await env.service.ingest("rules.md", b"## table: users\n\nNew text only.") == 1
    [(new_id, _, tags)] = env.chunks("rules.md")
    assert new_id not in old_ids
    assert list(tags) == ["users"]
    assert env.vector_count() == 1


async def test_sources_stay_independent(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    await env.service.ingest("b.md", b"## table: users\n\nOther text.")
    await env.service.ingest("a.md", b"## Short\n\nReplaced.")
    assert len(env.chunks("b.md")) == 1
    assert len(env.chunks("a.md")) == 1
    assert env.vector_count() == 2


async def test_delete_source_removes_text_and_vectors(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    await env.service.ingest("b.md", b"## table: users\n\nOther text.")
    assert await env.service.delete_source("a.md") == 3
    assert await env.service.delete_source("missing.md") == 0
    assert [r[1] for r in env.chunks()] == ["b.md"]
    assert env.vector_count() == 1


async def test_a_refused_document_changes_nothing(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    with pytest.raises(knowledge_module.DocumentError):
        await env.service.ingest("a.md", b"## table: bad name\n\nText.")
    assert len(env.chunks("a.md")) == 3
    assert env.vector_count() == 3


async def test_a_failed_vector_write_leaves_chunks_that_reindex_repairs(env: Env) -> None:
    broken = env.service_with(WordEmbedder(), BrokenStore())
    with pytest.raises(RuntimeError):
        await broken.ingest("a.md", USERS_DOC)
    assert len(env.chunks("a.md")) == 3  # the text is committed
    assert env.vector_count() == 0
    assert await env.service.reindex() == 3
    assert env.vector_count() == 3


async def test_a_failed_embedding_changes_nothing(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    with pytest.raises(RuntimeError):
        await env.service_with(BrokenEmbedder(), env.store).ingest("a.md", b"## A\n\nNew.")
    assert len(env.chunks("a.md")) == 3
    assert env.vector_count() == 3


async def test_reindex_rebuilds_vectors_after_they_are_lost(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    env.auth_db.execute("DELETE FROM chunk_embeddings")
    assert await env.service.reindex() == 3
    assert env.vector_count() == 3
    retrieval = await env.service.retrieve("paying customer enterprise", [])
    assert any("Enterprise accounts" in c.content for c in retrieval.chunks)


async def test_reindex_fills_a_fresh_store_of_another_kind(env: Env) -> None:
    await env.service.ingest("a.md", USERS_DOC)
    other = env.service_with(WordEmbedder(), MemoryVectorStore(384))
    assert await other.reindex() == 3
    retrieval = await other.retrieve("orders", ["users"])
    assert {c.source for c in retrieval.chunks} == {"a.md"}


# --- retrieve -----------------------------------------------------------------------------------


async def test_retrieve_returns_tagged_chunks_first_then_the_most_similar(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    filler = "\n\n".join(f"## Topic {i}\n\nunrelated filler number {i} words" for i in range(10))
    await env.service.ingest("filler.md", filler.encode())
    result = await env.service.retrieve("delete paying customer enterprise accounts", ["users"])
    matches = [c.match for c in result.chunks]
    assert matches[0] == "table"
    assert matches.count("table") == 1  # only the users chunk is tagged
    assert result.chunks[0].table_names == ["users"]
    similar = result.chunks[1:]
    assert 1 <= len(similar) <= 10
    assert "Enterprise accounts" in similar[0].content  # the business rules rank first
    scores = [c.score for c in similar]
    assert scores == sorted(scores, reverse=True)
    assert len(result.chunks) == len({c.chunk_id for c in result.chunks})


async def test_a_chunk_that_is_tagged_and_similar_appears_once_as_a_table_match(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("users are customers with a plan", ["users"])
    users = [c for c in result.chunks if c.table_names == ["users"]]
    assert len(users) == 1
    assert users[0].match == "table"
    assert users[0].score is not None  # it was also found by similarity


async def test_every_tagged_chunk_comes_back_even_when_there_are_more_than_five(env: Env) -> None:
    doc = "\n\n".join(f"## table: users\n\nnote {i}" for i in range(8))
    await env.service.ingest("notes.md", doc.encode())
    result = await env.service.retrieve("anything", ["users"])
    assert [c.match for c in result.chunks].count("table") == 8


async def test_retrieve_with_no_tables_still_returns_the_similar_chunks(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("paying customer", [])
    assert result.chunks
    assert {c.match for c in result.chunks} == {"similar"}


async def test_retrieve_normalizes_table_names(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("x", ["PUBLIC.Users"])
    assert [c.table_names for c in result.chunks if c.match == "table"] == [["users"]]


async def test_at_most_ten_similar_chunks_come_back(env: Env) -> None:
    doc = "\n\n".join(f"## Topic {i}\n\ncustomer plan {i}" for i in range(20))
    await env.service.ingest("many.md", doc.encode())
    result = await env.service.retrieve("customer plan", [])
    assert len(result.chunks) == 10


async def test_an_id_postgres_no_longer_has_is_dropped(env: Env) -> None:
    # A store without cascade (Qdrant) can hold a vector for a chunk that was deleted.
    memory = env.service_with(WordEmbedder(), MemoryVectorStore(384))
    await env.service.ingest("rules.md", USERS_DOC)
    await memory.reindex()
    env.auth_db.execute("DELETE FROM knowledge_chunks WHERE content LIKE '%%Enterprise%%'")
    result = await memory.retrieve("paying customer enterprise", [])
    assert result.chunks
    assert all("Enterprise" not in c.content for c in result.chunks)


async def test_refs_hold_the_origin_and_first_line_but_not_the_text(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("paying customer", ["users"])
    first = result.refs[0]
    assert first.match == "table"
    assert first.source == "rules.md"
    assert first.table_names == ["users"]
    assert first.first_line == "## table: users"
    assert "customers with a plan" not in repr(result.refs)
    assert result.duration_ms >= 0


async def test_a_store_that_raises_gives_retrieval_error_not_the_tagged_chunks(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    service = env.service_with(WordEmbedder(), BrokenStore())
    with pytest.raises(RetrievalError):
        await service.retrieve("x", ["users"])


async def test_an_embedder_that_raises_gives_retrieval_error_without_its_message(
    env: Env,
) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    service = env.service_with(BrokenEmbedder(), env.store)
    with pytest.raises(RetrievalError) as raised:
        await service.retrieve("x", ["users"])
    assert "secret@example.com" not in str(raised.value)


async def test_an_embedder_that_times_out_gives_retrieval_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SlowEmbedder(WordEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            await asyncio.sleep(5)
            return await super().embed(texts)

    monkeypatch.setattr(knowledge_module, "RETRIEVAL_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(RetrievalError, match="TimeoutError"):
        await env.service_with(SlowEmbedder(), env.store).retrieve("x", ["users"])


# --- knowledge_used -----------------------------------------------------------------------------


async def test_knowledge_used_is_saved_on_the_action(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("paying customer", ["users"])
    user = env.auth_db.make_user()
    agent = env.auth_db.make_agent(user.id)
    [(system_id,)] = env.auth_db.execute(
        "INSERT INTO systems (name, kind, connection_profile)"
        " VALUES (%s, 'postgres', 'neon') RETURNING id",
        (f"company-{uuid.uuid4().hex[:8]}",),
    )
    async with env.sessionmaker() as session, session.begin():
        action = await create_action(
            session, user_id=user.id, agent_id=agent.id, system_id=system_id, tool="query_database"
        )
        await knowledge_db.save_knowledge_used(session, action.id, result.refs)
    [(stored,)] = env.auth_db.execute(
        "SELECT knowledge_used FROM actions WHERE id = %s", (action.id,)
    )
    assert [r["chunk_id"] for r in stored] == [c.chunk_id for c in result.chunks]
    assert stored[0] == {
        "chunk_id": result.chunks[0].chunk_id,
        "source": "rules.md",
        "table_names": ["users"],
        "first_line": "## table: users",
        "match": "table",
        "score": result.chunks[0].score,
    }


async def test_odd_table_names_from_the_classifier_do_not_crash_retrieval(env: Env) -> None:
    await env.service.ingest("rules.md", USERS_DOC)
    result = await env.service.retrieve("x", ["order-items", "My Table", ""])
    assert all(c.match == "similar" for c in result.chunks)


async def test_two_ingests_of_one_source_at_once_leave_one_set_of_chunks(env: Env) -> None:
    await asyncio.gather(*(env.service.ingest("rules.md", USERS_DOC) for _ in range(4)))
    assert len(env.chunks("rules.md")) == 3
    assert env.vector_count() == 3
