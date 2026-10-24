"""BE-03 (blastproof-db part): the Compose init script creates the owner and app roles.

Needs `docker compose up -d`. If Postgres is down these tests fail at once rather than skip.
"""

import uuid

import asyncpg
import pytest

from tests.conftest import BLASTPROOF_APP_URL, BLASTPROOF_OWNER_URL

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


async def test_owner_can_create_and_drop_tables() -> None:
    table = f"role_check_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(BLASTPROOF_OWNER_URL)
    try:
        await conn.execute(f"CREATE TABLE public.{table} (id int)")
        await conn.execute(f"DROP TABLE public.{table}")
    finally:
        await conn.close()


async def test_app_can_connect_but_not_create_tables() -> None:
    conn = await asyncpg.connect(BLASTPROOF_APP_URL)
    try:
        assert await conn.fetchval("SELECT 1") == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("CREATE TABLE public.app_should_not_create (id int)")
    finally:
        await conn.close()


async def test_app_cannot_create_temporary_tables() -> None:
    # Deliberate (see 01-roles.sql); if a library ever needs temp tables, this shows why it fails.
    conn = await asyncpg.connect(BLASTPROOF_APP_URL)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("CREATE TEMPORARY TABLE app_temp (id int)")
    finally:
        await conn.close()


# ids keep the URLs (and any overridden password) out of test output.
@pytest.mark.parametrize("url", [BLASTPROOF_OWNER_URL, BLASTPROOF_APP_URL], ids=["owner", "app"])
async def test_roles_are_not_superusers(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
        )
        assert row is not None
        assert (row["rolsuper"], row["rolcreaterole"], row["rolcreatedb"]) == (False, False, False)
    finally:
        await conn.close()


async def test_public_cannot_connect_to_blastproof_database() -> None:
    conn = await asyncpg.connect(BLASTPROOF_OWNER_URL)
    try:
        acl = await conn.fetchval(
            "SELECT datacl::text FROM pg_database WHERE datname = 'blastproof'"
        )
        # An ACL entry with an empty grantee ("=...") would mean PUBLIC has access.
        assert acl is not None
        assert not any(entry.startswith("=") for entry in acl.strip("{}").split(","))
    finally:
        await conn.close()
