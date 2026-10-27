"""Step 20: `bootstrap` on a migrated scratch database and the local company_main (BE-120)."""

import logging
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from app.config import Settings
from app.core.security import hash_agent_key
from app.db.enums import UserRole
from app.services.bootstrap import BootstrapError, BootstrapReport, bootstrap
from tests.conftest import COMPANY_READER_URL, DEMO_COMPANY_DIR, AuthDb, mcp_settings
from tests.knowledge_helpers import WordEmbedder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

EMAIL = "first-admin@example.com"
PASSWORD = "a-long-enough-password"
SCHEMA_DOCS = DEMO_COMPANY_DIR / "schema_docs.md"


class PasswordSpy:
    def __init__(self, value: str = PASSWORD) -> None:
        self.calls = 0
        self.value = value

    def __call__(self) -> str:
        self.calls += 1
        return self.value


@pytest.fixture(autouse=True)
def clean(auth_db: AuthDb) -> Iterator[None]:
    def wipe() -> None:
        for table in ("knowledge_chunks", "agents", "systems", "users"):
            auth_db.execute(f"DELETE FROM {table}")  # noqa: S608

    wipe()
    yield
    wipe()


def company_table_count() -> int:
    with psycopg.connect(COMPANY_READER_URL) as conn:
        [(n,)] = conn.execute(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    return n


async def run(
    settings: Settings, password: PasswordSpy | None = None, **overrides: object
) -> BootstrapReport:
    args: dict[str, object] = {
        "admin_email": EMAIL,
        "admin_name": "First Admin",
        "password": password or PasswordSpy(),
        "schema_docs": SCHEMA_DOCS,
        "embedder": WordEmbedder(),
    }
    return await bootstrap(settings, **(args | overrides))  # type: ignore[arg-type]


def counts(auth_db: AuthDb) -> dict[str, int]:
    return {
        table: auth_db.execute(f"SELECT count(*) FROM {table}")[0][0]  # noqa: S608
        for table in ("users", "agents", "systems", "knowledge_chunks")
    }


async def test_a_fresh_run_creates_everything(auth_db: AuthDb) -> None:
    settings = mcp_settings(auth_db)
    report = await run(settings)

    assert (report.admin, report.agent) == ("created", "created")
    [(role, active, display)] = auth_db.execute(
        "SELECT role, is_active, display_name FROM users WHERE email = %s", (EMAIL,)
    )
    assert (role, active, display) == (UserRole.ADMIN.value, True, "First Admin")
    assert counts(auth_db)["agents"] == 1
    assert report.agent_key is not None
    [(key_hash,)] = auth_db.execute("SELECT api_key_hash FROM agents")
    assert key_hash == hash_agent_key(report.agent_key)
    assert report.setup is not None
    assert report.agent_key in report.setup[1] and report.setup[2].endswith(report.agent_key)
    [(name, profile)] = auth_db.execute("SELECT name, connection_profile FROM systems")
    assert (name, profile) == ("company", "local")
    assert (report.system_name, report.system_profile) == ("company", "local")
    assert report.scan_chunks == company_table_count()
    [(scan,)] = auth_db.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE source = 'schema_scan'"
    )
    assert scan == report.scan_chunks
    [(docs,)] = auth_db.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE source = 'schema_docs.md'"
    )
    assert docs == report.docs_chunks > 0


async def test_the_admin_can_log_in(auth_db: AuthDb) -> None:
    from app.db.session import make_engine, make_sessionmaker
    from app.services import auth as auth_service
    from tests.conftest import asyncpg_url

    await run(mcp_settings(auth_db))
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        async with make_sessionmaker(engine)() as session:
            user = await auth_service.authenticate(session, EMAIL, PASSWORD, "127.0.0.1")
        assert user.role == UserRole.ADMIN.value
    finally:
        await engine.dispose()


async def test_a_rerun_creates_nothing_new_and_shows_no_key(auth_db: AuthDb) -> None:
    settings = mcp_settings(auth_db)
    first = await run(settings)
    before = counts(auth_db)
    spy = PasswordSpy()
    second = await run(settings, spy)

    assert counts(auth_db) == before
    assert spy.calls == 0
    assert (second.admin, second.agent) == ("existing", "existing")
    assert second.agent_key is None and second.setup is None
    assert second.agent_key_prefix == first.agent_key_prefix
    assert (second.scan_chunks, second.docs_chunks) == (first.scan_chunks, first.docs_chunks)


@pytest.mark.parametrize(
    "make",
    [{"role": "member"}, {"role": "admin", "is_active": False}],
    ids=["member", "inactive-admin"],
)
async def test_an_existing_user_that_is_not_an_active_admin_is_refused(
    auth_db: AuthDb, make: dict[str, object]
) -> None:
    auth_db.make_user(email=EMAIL, **make)  # type: ignore[arg-type]
    spy = PasswordSpy()
    with pytest.raises(BootstrapError, match="not an active admin"):
        await run(mcp_settings(auth_db), spy)
    assert spy.calls == 0
    assert counts(auth_db) == {"users": 1, "agents": 0, "systems": 0, "knowledge_chunks": 0}


async def test_a_system_with_another_profile_is_refused_and_left_alone(auth_db: AuthDb) -> None:
    auth_db.execute("INSERT INTO systems (name, connection_profile) VALUES ('company', 'neon')")
    with pytest.raises(BootstrapError, match="'neon'.*'local'"):
        await run(mcp_settings(auth_db))
    assert auth_db.execute("SELECT connection_profile FROM systems") == [("neon",)]
    assert counts(auth_db)["knowledge_chunks"] == 0


async def test_an_unreachable_company_database_keeps_the_rest_and_a_rerun_finishes(
    auth_db: AuthDb,
) -> None:
    bad = mcp_settings(
        auth_db,
        local_company_reader_url="postgresql://blastproof_reader:x@localhost:1/company_main",
    )
    with pytest.raises(BootstrapError, match="could not be scanned"):
        await run(bad)
    assert counts(auth_db) == {"users": 1, "agents": 1, "systems": 1, "knowledge_chunks": 0}

    report = await run(mcp_settings(auth_db))
    assert (report.admin, report.agent) == ("existing", "existing")
    assert report.scan_chunks == company_table_count()
    assert counts(auth_db)["users"] == 1


async def test_a_missing_schema_docs_file_is_a_clear_error_and_changes_nothing(
    auth_db: AuthDb, tmp_path: Path
) -> None:
    with pytest.raises(BootstrapError, match="--schema-docs"):
        await run(mcp_settings(auth_db), schema_docs=tmp_path / "nope.md")
    assert counts(auth_db) == {"users": 0, "agents": 0, "systems": 0, "knowledge_chunks": 0}


async def test_no_password_or_key_reaches_the_logs(
    auth_db: AuthDb, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    report = await run(mcp_settings(auth_db))
    assert report.agent_key is not None
    seen = caplog.text + capfd.readouterr().err
    for secret in (PASSWORD, report.agent_key, "blastproof_app:", "blastproof_reader:"):
        assert secret not in seen
