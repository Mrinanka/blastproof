"""Creates the FastAPI app.

Run with: uv run uvicorn app.main:app --reload --env-file .env --log-config log_config.json
(`--env-file` puts .env into the process environment, where LangChain reads GOOGLE_API_KEY.)
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import lru_cache

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api import actions, auth, health, ready
from app.config import SET_EXPLICITLY_IN_PRODUCTION, Settings, get_settings
from app.core.clock import SystemClock
from app.core.errors import (
    ApiError,
    api_error_handler,
    use_error_envelope_in_openapi,
    validation_error_handler,
)
from app.core.request_id import RequestIdMiddleware
from app.db.models import EMBEDDING_DIMENSIONS
from app.db.session import make_engine, make_sessionmaker
from app.logging import configure_logging
from app.mcp.auth import AgentAuthMiddleware
from app.mcp.server import build_mcp_server, transport_security
from app.services.committer import CompanyCommitter
from app.services.embeddings import EmbeddingError, build_embedder
from app.services.impact import ImpactEngine
from app.services.judge import build_judge
from app.services.knowledge import KnowledgeService
from app.services.neon import BRANCH_LIFETIME, build_neon_clients, sweep_stale_shadows
from app.services.policy import load_policy
from app.services.shadow import ShadowRunner
from app.services.vectorstores import build_vector_store
from app.workflow.checkpointer import make_checkpointer, make_checkpointer_pool
from app.workflow.graph import WorkflowDeps, build_graph
from app.workflow.runner import WorkflowRunner

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # No network, database or model download here: startup must work offline (TEST-07).
    settings: Settings = app.state.settings
    logger.info("startup", app_env=settings.app_env)
    # The MCP transport's task group lives as long as the app (D-096); it opens no connection.
    async with app.state.mcp.session_manager.run():
        # Clear shadow copies a crashed run left behind, after startup so that a Neon outage never
        # stops the app from starting (tests skip it, TEST-07).
        sweep = None
        if settings.app_env != "test":
            sweep = asyncio.create_task(
                sweep_stale_shadows(
                    app.state.neon_clients, app.state.engine, older_than=BRANCH_LIFETIME
                )
            )
        # The checkpointer's pool opens here, not in create_app (tests skip it, TEST-07). With
        # wait=False a database that is down does not stop the app from starting; the graph then
        # fails closed on its first run.
        pool = app.state.checkpointer_pool
        maintain = None
        runner: WorkflowRunner | None = None
        if pool is not None:
            app.state.workflow = build_graph(app.state.workflow_deps, make_checkpointer(pool))
            runner = WorkflowRunner(
                app.state.workflow, app.state.sessionmaker, SystemClock(), settings
            )
            app.state.runner = runner
            if settings.app_env != "test":
                await pool.open(wait=False)
                # Recovery then the sweep, in the background: a database that is down at boot must
                # not stop startup (TEST-07). Until recovery is done the runner takes no new write
                # or decision, and /ready names `workflow`.
                maintain = asyncio.create_task(runner.maintain())
        # Load the embedding model after startup, so the first retrieval does not wait for it.
        warm_up = None
        if settings.app_env != "test":
            warm_up = asyncio.create_task(app.state.embedder.warm_up())
        try:
            yield
        finally:
            # Let running writes finish (uvicorn has already closed its socket); the rest are
            # cancelled and left for the next start's recovery.
            if runner is not None and settings.app_env != "test":
                await runner.drain()
            for task in (sweep, warm_up, maintain):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            if pool is not None and settings.app_env != "test":
                await pool.close()
    for client in app.state.neon_clients.values():
        await client.aclose()
    if app.state.engine is not None:
        await app.state.engine.dispose()
    logger.info("shutdown")


class MissingProductionSettingError(RuntimeError):
    pass


def _check_production_settings(settings: Settings) -> None:
    # Enforced here, not in Settings: Settings alone may be loaded with APP_ENV=production (tests).
    if settings.app_env != "production":
        return
    if settings.company_connection_profile == "local":
        raise MissingProductionSettingError(
            "APP_ENV=production needs COMPANY_CONNECTION_PROFILE=neon; refusing to start."
        )
    # The fake client clones a local database with a superuser login: never in production (SEC-17).
    if settings.fake_neon_admin_url is not None or settings.neon_client == "fake":
        raise MissingProductionSettingError(
            "APP_ENV=production refuses FAKE_NEON_ADMIN_URL and NEON_CLIENT=fake; "
            "refusing to start."
        )
    missing = [
        name
        for name, value in (
            ("BLASTPROOF_DATABASE_URL", settings.blastproof_database_url),
            ("CHECKPOINTER_DATABASE_URL", settings.checkpointer_database_url),
            ("JWT_SECRET", settings.jwt_secret),
            ("COMPANY_DB_READER_URL", settings.company_db_reader_url),
            ("COMPANY_DB_WRITER_URL", settings.company_db_writer_url),
            ("NEON_API_KEY", settings.neon_api_key),
            ("NEON_PROJECT_ID", settings.neon_project_id),
            ("NEON_PARENT_BRANCH_ID", settings.neon_parent_branch_id),
        )
        if value is None
    ]
    # The model and price defaults are for dev machines only (D-165).
    missing += [
        env
        for name, env in SET_EXPLICITLY_IN_PRODUCTION.items()
        if name not in settings.explicit_fields
    ]
    if missing:
        raise MissingProductionSettingError(
            f"APP_ENV=production needs {', '.join(missing)}; refusing to start."
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    _check_production_settings(settings)

    app = FastAPI(title="Blastproof", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    # A bad rules file stops startup (PolicyFileError). Reads one local file: still offline.
    app.state.policy = load_policy(settings.policy_file)
    # Building the Neon clients and the shadow runner opens no connection (httpx opens none).
    app.state.neon_clients = build_neon_clients(settings, SystemClock())
    app.state.shadow_runner = ShadowRunner(
        policy=app.state.policy,
        clients=app.state.neon_clients,
        max_concurrent=settings.max_concurrent_shadows,
        timeout_seconds=settings.shadow_timeout_seconds,
    )
    # The engine connects lazily, on the first query, so startup stays offline (TEST-07).
    url = settings.blastproof_database_url
    app.state.engine = make_engine(url.get_secret_value()) if url is not None else None
    app.state.sessionmaker = make_sessionmaker(app.state.engine) if app.state.engine else None

    # Building the embedder and store loads no model and opens no connection. Only the model's
    # vector size is checked (from fastembed's list, no download): a mismatch stops startup.
    app.state.embedder = build_embedder(settings)
    if app.state.embedder.dimensions != EMBEDDING_DIMENSIONS:
        raise EmbeddingError(
            f"EMBEDDING_MODEL {settings.embedding_model!r} makes {app.state.embedder.dimensions}"
            f"-dimension vectors, but the database column holds {EMBEDDING_DIMENSIONS}."
        )
    # Builds no model and calls nothing: the summary model is created on the first summary. A model
    # without an LLM_PRICES entry stops startup (LlmConfigError).
    app.state.impact = ImpactEngine(settings, app.state.policy)
    # Same for the judge: JUDGE_PROVIDER must name a judge and JUDGE_MODEL must have a price, or
    # startup stops (JudgeConfigError, LlmConfigError).
    app.state.judge = build_judge(settings, app.state.policy)

    store = build_vector_store(settings, app.state.sessionmaker)
    app.state.knowledge = (
        KnowledgeService(app.state.sessionmaker, app.state.embedder, store)
        if store is not None and app.state.sessionmaker is not None
        else None
    )

    # The write workflow (step 14). Only the closed pool is built here: the saver needs a running
    # event loop, so the lifespan builds it and the graph. Neither opens a connection (TEST-07);
    # the lifespan opens the pool. The runner (step 15) starts it; step 17 feeds it.
    checkpointer_url = settings.checkpointer_database_url
    app.state.checkpointer_pool = None
    app.state.workflow_deps = None
    app.state.workflow = None
    app.state.runner = None
    if checkpointer_url is not None and app.state.sessionmaker is not None:
        app.state.checkpointer_pool = make_checkpointer_pool(checkpointer_url.get_secret_value())
        app.state.workflow_deps = WorkflowDeps(
            sessionmaker=app.state.sessionmaker,
            policy=app.state.policy,
            shadow=app.state.shadow_runner,
            impact=app.state.impact,
            judge=app.state.judge,
            knowledge=app.state.knowledge,
            committer=CompanyCommitter(app.state.sessionmaker, settings, SystemClock()),
            clock=SystemClock(),
            settings=settings,
        )

    # One MCP server per app (D-096). Its single /mcp route joins FastAPI's own router, so unknown
    # paths still get FastAPI's 404 and /mcp stays out of the OpenAPI schema.
    app.state.mcp = build_mcp_server(settings, app.state.sessionmaker)
    mcp_app = app.state.mcp.streamable_http_app(
        stateless_http=True, json_response=False, transport_security=transport_security(settings)
    )
    app.router.routes.extend(mcp_app.routes)

    # Added first, so it runs inside RequestIdMiddleware (the request ID stays outermost).
    app.add_middleware(AgentAuthMiddleware, sessionmaker=app.state.sessionmaker)
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    use_error_envelope_in_openapi(app)
    app.include_router(health.router)
    app.include_router(ready.router)
    app.include_router(auth.router)
    app.include_router(actions.router)
    return app


@lru_cache
def _default_app() -> FastAPI:
    return create_app()


def __getattr__(name: str) -> FastAPI:
    # `app` is built on first access (uvicorn's "app.main:app"), not at import time, so importing
    # this module (as tests do) never reads backend/.env. PEP 562 module __getattr__.
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
