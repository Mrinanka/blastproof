"""Creates the FastAPI app.

Run with: uv run uvicorn app.main:app --reload --log-config log_config.json
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

import structlog
from fastapi import FastAPI

from app.api import health
from app.config import Settings, get_settings
from app.core.request_id import RequestIdMiddleware
from app.logging import configure_logging

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # No network, database or model download here: startup must work offline (TEST-07).
    settings: Settings = app.state.settings
    logger.info("startup", app_env=settings.app_env)
    yield
    logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Blastproof", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)
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
