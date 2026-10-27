"""Helpers for the step 19 route tests: an app with a knowledge service that needs no model."""

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import EMBEDDING_DIMENSIONS
from app.main import create_app
from app.services.knowledge import KnowledgeService
from app.services.vectorstores.memory import MemoryVectorStore
from tests.conftest import AuthDb, mcp_settings, with_client_ip
from tests.knowledge_helpers import WordEmbedder


def build_admin_app(auth_db: AuthDb, **overrides: object) -> FastAPI:
    """The real app on the scratch database, the local company reader, and in-memory vectors."""
    app = create_app(mcp_settings(auth_db, **overrides))
    app.state.knowledge = KnowledgeService(
        app.state.sessionmaker, WordEmbedder(), MemoryVectorStore(EMBEDDING_DIMENSIONS)
    )
    return app


@pytest.fixture
def admin_settings(auth_db: AuthDb) -> Settings:
    return mcp_settings(auth_db)


def client_for(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(with_client_ip(app)) as client:
        yield client


def logged_text(caplog: pytest.LogCaptureFixture) -> str:
    """Everything the app logged, as one string (caplog, because loggers are cached).

    The test client's own request lines (httpx) are left out: they are the test's, not the app's.
    """
    records = [rec for rec in caplog.records if not rec.name.startswith("httpx")]
    assert records, "nothing was captured, so this test would prove nothing"
    return " ".join(f"{rec.getMessage()} {rec.__dict__}" for rec in records)


def at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
