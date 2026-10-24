"""Creates the FastAPI app.

Run with: uv run uvicorn app.main:app --reload --log-config log_config.json
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api import auth, health
from app.config import Settings, get_settings
from app.core.errors import (
    ApiError,
    api_error_handler,
    use_error_envelope_in_openapi,
    validation_error_handler,
)
from app.core.request_id import RequestIdMiddleware
from app.db.session import make_engine, make_sessionmaker
from app.logging import configure_logging

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # No network, database or model download here: startup must work offline (TEST-07).
    settings: Settings = app.state.settings
    logger.info("startup", app_env=settings.app_env)
    yield
    if app.state.engine is not None:
        await app.state.engine.dispose()
    logger.info("shutdown")


class MissingProductionSettingError(RuntimeError):
    pass


def _check_production_settings(settings: Settings) -> None:
    # Enforced here, not in Settings: Settings alone may be loaded with APP_ENV=production (tests).
    missing = [
        name
        for name, value in (
            ("BLASTPROOF_DATABASE_URL", settings.blastproof_database_url),
            ("JWT_SECRET", settings.jwt_secret),
        )
        if value is None
    ]
    if settings.app_env == "production" and missing:
        raise MissingProductionSettingError(
            f"APP_ENV=production needs {', '.join(missing)}; refusing to start."
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    _check_production_settings(settings)

    app = FastAPI(title="Blastproof", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    # The engine connects lazily, on the first query, so startup stays offline (TEST-07).
    url = settings.blastproof_database_url
    app.state.engine = make_engine(url.get_secret_value()) if url is not None else None
    app.state.sessionmaker = make_sessionmaker(app.state.engine) if app.state.engine else None
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    use_error_envelope_in_openapi(app)
    app.include_router(health.router)
    app.include_router(auth.router)
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
