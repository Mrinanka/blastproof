"""`/ready` names the checkpointer and the workflow when a checkpointer is configured, and the
lifespan starts no recovery in the test environment."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import AuthDb, asyncpg_url

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


def app_for(db: AuthDb):  # type: ignore[no-untyped-def]
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=asyncpg_url(db.app_url),
        checkpointer_database_url=db.app_url,
    )
    return create_app(settings)


async def ready_message(app) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
    body = response.json()
    return response.status_code, body.get("error", {}).get("message", "")


async def test_pool_open_and_recovered_is_ready_for_both(auth_db: AuthDb) -> None:
    app = app_for(auth_db)
    async with app.router.lifespan_context(app):
        await app.state.checkpointer_pool.open(wait=True)
        try:
            app.state.runner.mark_recovered()
            _, message = await ready_message(app)
        finally:
            await app.state.checkpointer_pool.close()
    assert "checkpointer" not in message and "workflow" not in message


async def test_a_closed_pool_names_the_checkpointer(auth_db: AuthDb) -> None:
    app = app_for(auth_db)
    async with app.router.lifespan_context(app):
        app.state.runner.mark_recovered()
        status, message = await ready_message(app)
    assert status == 503 and "checkpointer" in message and "workflow" not in message


async def test_recovery_not_finished_names_the_workflow(auth_db: AuthDb) -> None:
    app = app_for(auth_db)
    async with app.router.lifespan_context(app):
        await app.state.checkpointer_pool.open(wait=True)
        try:
            status, message = await ready_message(app)
        finally:
            await app.state.checkpointer_pool.close()
    assert status == 503 and "workflow" in message and "checkpointer" not in message


def test_the_test_environment_starts_no_recovery(auth_db: AuthDb) -> None:
    app = app_for(auth_db)
    with TestClient(app) as client:
        runner = client.app.state.runner  # type: ignore[attr-defined]
        assert runner is not None and not runner.accepting
        assert runner._tasks == set()
