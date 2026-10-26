"""Step 11 Done-when, with the real embedding model (downloads it the first time, then caches it).

The schema scan plus schema_docs.md go into PgVectorStore; retrieving for "Clean up inactive users"
must bring back the business-rules chunk (paying customers, Enterprise accounts) by similarity.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import DEFAULT_EMBEDDING_CACHE_DIR, DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.db import knowledge as knowledge_db
from app.db.actions import create_action
from app.db.session import make_engine, make_sessionmaker
from app.services.classifier import classify
from app.services.embeddings import FastEmbedEmbedder
from app.services.knowledge import (
    SCHEMA_DOCS_SOURCE,
    SIMILAR_CHUNKS,
    KnowledgeService,
    Retrieval,
)
from app.services.policy import Route, evaluate_pass_one, load_policy
from app.services.vectorstores.pgvector import PgVectorStore
from evals.schema import SqlScenario, load_scenario_file
from tests.conftest import (
    DEMO_COMPANY_DIR,
    SCENARIOS_DIR,
    AuthDb,
    asyncpg_url,
    mcp_settings,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

REPLIT_TABLES = [
    "users",
    "orders",
    "order_items",
    "subscriptions",
    "payments",
    "support_tickets",
    "customer_notes",
    "invoices",
]
INTENT = "Clean up inactive users"


def schema_docs() -> bytes:
    return Path(DEMO_COMPANY_DIR / "schema_docs.md").read_bytes()


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    """The real model, shared by the module so it loads once."""
    return FastEmbedEmbedder("BAAI/bge-small-en-v1.5", DEFAULT_EMBEDDING_CACHE_DIR)


@pytest.fixture
async def service(
    auth_db: AuthDb, embedder: FastEmbedEmbedder
) -> AsyncIterator[tuple[KnowledgeService, AuthDb]]:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    sessionmaker = make_sessionmaker(engine)
    auth_db.execute("DELETE FROM knowledge_chunks")
    knowledge = KnowledgeService(sessionmaker, embedder, PgVectorStore(sessionmaker))
    await knowledge.scan(mcp_settings(auth_db))
    await knowledge.ingest(SCHEMA_DOCS_SOURCE, schema_docs())
    try:
        yield knowledge, auth_db
    finally:
        auth_db.execute("DELETE FROM knowledge_chunks")
        await engine.dispose()


def business_rules(result: Retrieval) -> list:
    return [c for c in result.chunks if c.content.startswith("## business rules")]


def assert_has_business_rules(result: Retrieval) -> None:
    [rules] = business_rules(result)
    assert rules.match == "similar"  # reached through the vector search, not a table tag
    assert "paying customer" in rules.content
    assert "Enterprise accounts" in rules.content
    assert "must never be deleted" in rules.content


async def test_the_real_model_makes_384_floats_per_text(embedder: FastEmbedEmbedder) -> None:
    vectors = await embedder.embed(["one", "two words"])
    assert [len(v) for v in vectors] == [384, 384]
    assert all(isinstance(x, float) for x in vectors[0])


async def test_clean_up_inactive_users_retrieves_the_business_rules(
    service: tuple[KnowledgeService, AuthDb],
) -> None:
    knowledge, _ = service
    result = await knowledge.retrieve(INTENT, REPLIT_TABLES)
    assert_has_business_rules(result)
    tagged = {c.table_names[0] for c in result.chunks if c.match == "table" and c.table_names}
    assert set(REPLIT_TABLES) <= tagged  # every affected table's notes come too
    assert len({c.chunk_id for c in result.chunks}) == len(result.chunks)


async def test_the_business_rules_come_back_with_only_users_tagged(
    service: tuple[KnowledgeService, AuthDb],
) -> None:
    knowledge, _ = service
    assert_has_business_rules(await knowledge.retrieve(INTENT, ["users"]))


async def test_every_scenario_that_deletes_from_users_retrieves_the_business_rules(
    service: tuple[KnowledgeService, AuthDb], frozen_clock: FrozenClock
) -> None:
    knowledge, _ = service
    scenarios = []
    for name in ("incidents.yaml", "labeled.yaml"):
        for scenario in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if isinstance(scenario, SqlScenario):
                kind = classify(scenario.sql, now=frozen_clock.now())
                if "delete" in kind.statement_types and "users" in kind.target_tables:
                    scenarios.append(scenario)
    assert len(scenarios) >= 2
    for scenario in scenarios:
        result = await knowledge.retrieve(scenario.intent, ["users"])
        assert business_rules(result), scenario.id


async def test_knowledge_used_is_saved_on_a_real_action_row(
    service: tuple[KnowledgeService, AuthDb],
) -> None:
    knowledge, auth_db = service
    result = await knowledge.retrieve(INTENT, REPLIT_TABLES)
    user = auth_db.make_user()
    agent = auth_db.make_agent(user.id)
    [(system_id,)] = auth_db.execute(
        "INSERT INTO systems (name, kind, connection_profile)"
        " VALUES ('company-dw', 'postgres', 'neon')"
        " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id"
    )
    async with knowledge._sessionmaker() as session, session.begin():
        action = await create_action(
            session, user_id=user.id, agent_id=agent.id, system_id=system_id, tool="query_database"
        )
        await knowledge_db.save_knowledge_used(session, action.id, result.refs)
    [(stored,)] = auth_db.execute("SELECT knowledge_used FROM actions WHERE id = %s", (action.id,))
    assert len(stored) == len(result.chunks)
    rules = [r for r in stored if r["first_line"] == "## business rules"]
    assert len(rules) == 1
    assert rules[0]["match"] == "similar"
    assert rules[0]["source"] == SCHEMA_DOCS_SOURCE
    assert all("content" not in r for r in stored)


# Measured 2026-10-26 (k=10): the five users deletes that reach retrieval rank 1 to 6; the three
# that pass 1 blocks rank 2 to 8. A margin of three places keeps a small model or chunking change
# from silently pushing the rules out of the top k.
MAX_RULES_RANK = SIMILAR_CHUNKS - 3


async def test_the_business_rules_rank_well_inside_the_top_k(
    service: tuple[KnowledgeService, AuthDb], frozen_clock: FrozenClock
) -> None:
    """Gate audit: the rules arrive only through similarity (they carry no table tag). With no
    tables, every chunk comes from the vector search, best first, so the position is the rank.
    Only writes that pass 1 sends to preview are retrieved for, so only they must rank well."""
    knowledge, _ = service
    policy = load_policy(DEFAULT_POLICY_FILE)
    ranks: dict[str, int | None] = {}
    for name in ("incidents.yaml", "labeled.yaml"):
        for scenario in load_scenario_file(SCENARIOS_DIR / name).scenarios:
            if not isinstance(scenario, SqlScenario):
                continue
            kind = classify(scenario.sql, now=frozen_clock.now())
            if not ("delete" in kind.statement_types and "users" in kind.target_tables):
                continue
            if evaluate_pass_one(policy, kind).route != Route.PREVIEW:
                continue
            result = await knowledge.retrieve(scenario.intent, [])
            assert all(c.match == "similar" for c in result.chunks)
            ranks[scenario.id] = next(
                (i for i, c in enumerate(result.chunks, 1) if c in business_rules(result)), None
            )
    assert len(ranks) >= 4, ranks
    assert all(r is not None and r <= MAX_RULES_RANK for r in ranks.values()), ranks
