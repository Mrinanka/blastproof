"""First-run setup (backend plan step 20, BE-120): the first admin, an agent, the main system and
the knowledge base.

Every part is get-or-create, so a run that stopped half way (say, the company database was down
during the schema scan) is finished by running it again. Nothing is rewritten: an existing admin
and agent are reused, and a main system stored with another profile is refused, not fixed.
No URL, password, key or SQL reaches a log; the agent's key is in the report, once.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import agents as agents_db
from app.db import systems as systems_db
from app.db import users as users_db
from app.db.enums import UserRole
from app.db.models import System
from app.db.session import make_engine, make_sessionmaker
from app.services import auth as auth_service
from app.services.agent_admin import setup_lines
from app.services.embeddings import Embedder, build_embedder
from app.services.knowledge import KnowledgeService
from app.services.systems import MAIN_SYSTEM_NAME, SystemProfileMismatchError, ensure_main_system
from app.services.vectorstores import VectorStore, build_vector_store

logger = structlog.get_logger()

SCHEMA_DOCS_SOURCE = "schema_docs.md"


class BootstrapError(Exception):
    """The bootstrap cannot run or did not finish; the message is safe to show."""


@dataclass(frozen=True)
class BootstrapReport:
    admin: Literal["created", "existing"]
    admin_email: str
    agent: Literal["created", "existing"]
    agent_name: str
    agent_key_prefix: str
    agent_key: str | None  # only when created; shown once
    setup: tuple[str, str, str] | None  # mcp_url, `claude mcp add`, hook env line
    system_name: str
    system_profile: str
    scan_chunks: int
    docs_chunks: int


async def _ensure_admin(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    email: str,
    name: str,
    password: Callable[[], str],
) -> tuple[Literal["created", "existing"], str]:
    email = auth_service.normalise_email(email)
    async with sessionmaker() as session:
        user = await users_db.get_user_by_email(session, email)
    if user is None:
        async with sessionmaker() as session:
            await auth_service.create_user(
                session,
                email=email,
                display_name=name,
                role=UserRole.ADMIN,
                password=password(),
            )
        return "created", email
    if user.role != UserRole.ADMIN.value or not user.is_active:
        raise BootstrapError(f"{email} exists but is not an active admin; refusing to use it.")
    return "existing", email


async def _ensure_agent(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, *, email: str, name: str
) -> tuple[Literal["created", "existing"], str, str | None, tuple[str, str, str] | None]:
    async with sessionmaker() as session:
        owner = await users_db.get_user_by_email(session, email)
        assert owner is not None
        agent = await agents_db.get_agent_by_owner_and_name(session, owner.id, name)
    if agent is not None:
        return "existing", agent.key_prefix, None, None
    async with sessionmaker() as session:
        created = await auth_service.create_agent(session, owner_email=email, name=name)
    return (
        "created",
        created.agent.key_prefix,
        created.key.key,
        setup_lines(settings, created.key.key),
    )


async def _ensure_system(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> System:
    try:
        async with sessionmaker.begin() as session:
            return await ensure_main_system(session, settings)
    except SystemProfileMismatchError:
        async with sessionmaker() as session:
            stored = await systems_db.get_profile_by_name(session, MAIN_SYSTEM_NAME)
        raise BootstrapError(
            f"The main system is stored with the {stored!r} profile, but this deployment uses "
            f"{settings.company_connection_profile!r}. Set COMPANY_CONNECTION_PROFILE to match, "
            "or use a fresh Blastproof database; the stored row is left unchanged."
        ) from None


async def bootstrap(
    settings: Settings,
    *,
    admin_email: str,
    admin_name: str,
    password: Callable[[], str],
    schema_docs: Path,
    agent_name: str = "claude-code",
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> BootstrapReport:
    """Admin, agent, system, schema scan, schema docs, in that order. `password` is called at
    most once, and only when the admin has to be created."""
    if settings.blastproof_database_url is None:
        raise BootstrapError(
            "BLASTPROOF_DATABASE_URL is not set. See backend/.env.example for the local value."
        )
    try:
        docs = await asyncio.to_thread(schema_docs.read_bytes)
    except OSError:
        raise BootstrapError(
            f"The schema docs file could not be read; check --schema-docs ({schema_docs.name})."
        ) from None
    engine = make_engine(settings.blastproof_database_url.get_secret_value())
    try:
        sessionmaker = make_sessionmaker(engine)
        admin, email = await _ensure_admin(
            sessionmaker, email=admin_email, name=admin_name, password=password
        )
        agent, prefix, key, setup = await _ensure_agent(
            sessionmaker, settings, email=email, name=agent_name
        )
        system = await _ensure_system(sessionmaker, settings)
        vector_store = store or build_vector_store(settings, sessionmaker)
        assert vector_store is not None
        service = KnowledgeService(sessionmaker, embedder or build_embedder(settings), vector_store)
        try:
            scan_chunks = await service.scan(settings, profile=system.connection_profile)
        except Exception as exc:  # the company database is unreachable or unreadable
            logger.error("bootstrap_scan_failed", error_type=type(exc).__name__)
            raise BootstrapError(
                "The company database could not be scanned. The admin, agent and system are "
                "saved; fix the company database settings and run bootstrap again."
            ) from None
        docs_chunks = await service.ingest(SCHEMA_DOCS_SOURCE, docs)
    finally:
        await engine.dispose()
    logger.info("bootstrap_done", admin=admin, agent=agent)
    return BootstrapReport(
        admin=admin,
        admin_email=email,
        agent=agent,
        agent_name=agent_name,
        agent_key_prefix=prefix,
        agent_key=key,
        setup=setup,
        system_name=system.name,
        system_profile=system.connection_profile,
        scan_chunks=scan_chunks,
        docs_chunks=docs_chunks,
    )
