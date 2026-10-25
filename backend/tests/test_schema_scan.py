"""Step 11: the schema scan makes one tagged chunk per company table (BE-59)."""

import pytest

from app.db.session import make_engine, make_sessionmaker
from app.services.knowledge import SCHEMA_SCAN_SOURCE, KnowledgeService, scan_schema
from app.services.vectorstores.pgvector import PgVectorStore
from tests.conftest import AuthDb, asyncpg_url, mcp_settings
from tests.knowledge_helpers import WordEmbedder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

TABLES = {
    "customer_notes",
    "feature_flags",
    "invoices",
    "order_items",
    "orders",
    "organizations",
    "payments",
    "products",
    "subscriptions",
    "support_tickets",
    "users",
}


async def scanned(auth_db: AuthDb) -> dict[str, str]:
    drafts = await scan_schema(mcp_settings(auth_db))
    assert {d.source for d in drafts} == {SCHEMA_SCAN_SOURCE}
    return {d.table_names[0]: d.content for d in drafts}


async def test_one_tagged_chunk_per_public_table(auth_db: AuthDb) -> None:
    drafts = await scan_schema(mcp_settings(auth_db))
    assert len(drafts) == 11
    assert {d.table_names for d in drafts} == {(name,) for name in TABLES}


async def test_the_gateways_own_schema_is_not_described(auth_db: AuthDb) -> None:
    chunks = await scanned(auth_db)
    for content in chunks.values():
        assert "applied_actions" not in content
        assert "seed_info" not in content
        assert "blastproof" not in content.lower()


async def test_columns_types_nullability_and_the_primary_key(auth_db: AuthDb) -> None:
    users = (await scanned(auth_db))["users"]
    assert users.startswith("## table: users\n")
    assert "- id: bigint, not null" in users
    assert "- organization_id: bigint, nullable" in users
    assert "- last_login_at: timestamp with time zone, not null" in users
    assert "Primary key: id" in users


async def test_foreign_keys_out_and_in_with_their_delete_actions(auth_db: AuthDb) -> None:
    chunks = await scanned(auth_db)
    assert "- organization_id -> organizations(id), on delete: set null" in chunks["users"]
    for child, action in (("orders", "cascade"), ("invoices", "set null")):
        assert f"- {child}(user_id), on delete: {action}" in chunks["users"]
    assert "Referenced by" in chunks["users"]
    assert "- user_id -> users(id), on delete: cascade" in chunks["orders"]
    assert "- order_id -> orders(id), on delete: cascade" in chunks["order_items"]
    # products has no foreign key in either direction.
    assert "References" not in chunks["products"]
    assert "Referenced by" not in chunks["products"]


async def test_a_rescan_replaces_the_chunks(auth_db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    auth_db.execute("DELETE FROM knowledge_chunks")
    try:
        service = KnowledgeService(
            make_sessionmaker(engine), WordEmbedder(), PgVectorStore(make_sessionmaker(engine))
        )
        settings = mcp_settings(auth_db)
        assert await service.scan(settings) == 11
        first = {r[0] for r in auth_db.execute("SELECT id FROM knowledge_chunks")}
        assert await service.scan(settings) == 11
        second = {r[0] for r in auth_db.execute("SELECT id FROM knowledge_chunks")}
        assert len(second) == 11
        assert first.isdisjoint(second)
        [(vectors,)] = auth_db.execute("SELECT count(*) FROM chunk_embeddings")
        assert vectors == 11
    finally:
        auth_db.execute("DELETE FROM knowledge_chunks")
        await engine.dispose()


async def test_the_scan_reads_through_the_reader_role_only(auth_db: AuthDb) -> None:
    # No writer or admin URL is configured: the scan must not need one.
    settings = mcp_settings(auth_db)
    assert settings.local_company_writer_url is None
    assert settings.fake_neon_admin_url is None
    assert len(await scan_schema(settings)) == 11
