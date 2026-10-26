"""CHECKPOINTER_DATABASE_URL (step 14): validation, the production requirement, and no connection
at startup (TEST-07)."""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import MissingProductionSettingError, create_app
from app.workflow.checkpointer import make_checkpointer, make_checkpointer_pool

PLAIN = "postgresql://blastproof_app:s3cretpw@db.invalid:5432/blastproof"
ASYNC = "postgresql+asyncpg://blastproof_app:s3cretpw@db.invalid:5432/blastproof"


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)  # type: ignore[arg-type]


def test_unset_and_empty_mean_unset() -> None:
    assert settings().checkpointer_database_url is None
    assert settings(checkpointer_database_url="").checkpointer_database_url is None


def test_a_plain_url_is_accepted() -> None:
    url = settings(checkpointer_database_url=PLAIN).checkpointer_database_url
    assert url is not None and url.get_secret_value() == PLAIN


@pytest.mark.parametrize(
    "bad", [ASYNC, "mysql://u:s3cretpw@db/x", "s3cretpw", "http://u:s3cretpw@db/x"]
)
def test_any_other_scheme_is_refused_without_showing_the_value(bad: str) -> None:
    with pytest.raises(ValidationError) as caught:
        settings(checkpointer_database_url=bad)
    text = str(caught.value)
    assert "CHECKPOINTER_DATABASE_URL" in text
    assert "s3cretpw" not in text


def test_the_value_is_hidden_in_repr() -> None:
    assert "s3cretpw" not in repr(settings(checkpointer_database_url=PLAIN))


def production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "neon_client": "api",
        "blastproof_database_url": ASYNC,
        "checkpointer_database_url": PLAIN,
        "jwt_secret": "x" * 40,
        "company_db_reader_url": "postgresql://blastproof_reader:x@db.invalid/neondb",
        "company_db_writer_url": "postgresql://blastproof_writer:x@db.invalid/neondb",
        "neon_api_key": "k",
        "neon_project_id": "p",
        "neon_parent_branch_id": "b",
        "summary_model": "anthropic:claude-haiku-5-5",
        "judge_model": "anthropic:claude-haiku-5-5",
        "llm_prices": {
            "anthropic:claude-haiku-5-5": {"input_per_million": 1, "output_per_million": 5}
        },
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[arg-type]


def test_production_needs_the_url() -> None:
    create_app(production())  # builds; never connects
    with pytest.raises(MissingProductionSettingError, match="CHECKPOINTER_DATABASE_URL"):
        create_app(production(checkpointer_database_url=None))


def test_no_url_means_no_workflow(no_network) -> None:  # type: ignore[no-untyped-def]
    with TestClient(create_app(settings())) as client:
        assert client.app.state.workflow is None  # type: ignore[attr-defined]
        assert client.app.state.checkpointer_pool is None  # type: ignore[attr-defined]


def test_building_the_app_opens_no_connection(no_network) -> None:  # type: ignore[no-untyped-def]
    """With APP_ENV=test the lifespan builds the graph but never opens the pool (TEST-07)."""
    app = create_app(
        settings(
            blastproof_database_url=ASYNC,
            checkpointer_database_url=PLAIN,
        )
    )
    pool = app.state.checkpointer_pool
    assert pool is not None and pool.closed
    with TestClient(app) as client:
        assert client.app.state.workflow is not None  # type: ignore[attr-defined]
        assert pool.closed
    assert no_network.blocked == []


def test_the_pool_is_built_closed_with_the_settings_the_spike_found() -> None:
    pool = make_checkpointer_pool(PLAIN)
    assert pool.closed
    assert pool.kwargs["autocommit"] is True
    assert pool.kwargs["prepare_threshold"] == 0
    assert pool.kwargs["row_factory"] is not None
    assert pool._check is not None  # a pool without a check keeps handing out dead connections


@dataclass
class Sneaky:
    value: int


async def test_the_saver_uses_strict_serde() -> None:
    """A dataclass that sneaks into the state must not come back as an instance (D-178, D-186)."""
    saver = make_checkpointer(make_checkpointer_pool(PLAIN))
    revived = saver.serde.loads_typed(saver.serde.dumps_typed({"inner": Sneaky(1)}))
    assert not isinstance(revived["inner"], Sneaky)
