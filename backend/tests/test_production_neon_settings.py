"""Step 10, SEC-17: production needs the Neon settings and refuses the fake client."""

import pytest

import app.main as main
from app.config import Settings
from tests.conftest import TEST_JWT_SECRET

# `main` is read at call time: test_main reloads the module, which makes a new exception class.
COMPLETE = {
    "app_env": "production",
    "blastproof_database_url": "postgresql+asyncpg://blastproof_app:x@db.invalid/blastproof",
    "jwt_secret": TEST_JWT_SECRET,
    "company_db_reader_url": "postgresql://blastproof_reader:x@db.invalid/neondb",
    "company_db_writer_url": "postgresql://blastproof_writer:x@db.invalid/neondb",
    "neon_api_key": "napi-key",
    "neon_project_id": "project",
    "neon_parent_branch_id": "br-parent",
}


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(COMPLETE | overrides))  # type: ignore[arg-type]


def test_a_complete_production_config_builds_without_connecting() -> None:
    app = main.create_app(settings())
    assert set(app.state.neon_clients) == {"neon"}
    assert app.state.shadow_runner is not None


@pytest.mark.parametrize(
    ("name", "env"),
    [
        ("company_db_writer_url", "COMPANY_DB_WRITER_URL"),
        ("neon_api_key", "NEON_API_KEY"),
        ("neon_project_id", "NEON_PROJECT_ID"),
        ("neon_parent_branch_id", "NEON_PARENT_BRANCH_ID"),
    ],
)
def test_production_needs_each_neon_setting(name: str, env: str) -> None:
    with pytest.raises(main.MissingProductionSettingError, match=env):
        main.create_app(settings(**{name: None}))


def test_production_refuses_the_fake_admin_url() -> None:
    with pytest.raises(main.MissingProductionSettingError, match="FAKE_NEON_ADMIN_URL"):
        main.create_app(
            settings(fake_neon_admin_url="postgresql://company:company@localhost/postgres")
        )


def test_production_refuses_the_fake_client() -> None:
    with pytest.raises(main.MissingProductionSettingError, match="NEON_CLIENT=fake"):
        main.create_app(settings(neon_client="fake"))
