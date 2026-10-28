"""Guards on fly.toml (Deploy: OPS-08 to OPS-11, BE-86, ARCH-16, NFR-06). Read with tomllib."""

import re
import tomllib
from typing import Any

import pytest

from app.config import Settings
from app.workflow.runner import DRAIN_SECONDS
from tests.test_dockerfile import REPO_ROOT

FLY_PATH = REPO_ROOT / "fly.toml"


@pytest.fixture(scope="module")
def fly() -> dict[str, Any]:
    return tomllib.loads(FLY_PATH.read_text(encoding="utf-8"))


def test_migrations_run_as_the_release_command_on_a_rolling_deploy(fly: dict[str, Any]) -> None:
    assert fly["deploy"]["release_command"] == "alembic upgrade head"
    assert fly["deploy"]["strategy"] == "rolling"


def test_shutdown_is_sigterm_with_time_to_drain(fly: dict[str, Any]) -> None:
    assert fly["kill_signal"] == "SIGTERM"
    assert fly["kill_timeout"] == 60
    # uvicorn's 10 s for open connections (Dockerfile), the runner's drain, and a margin.
    assert fly["kill_timeout"] >= 10 + DRAIN_SECONDS + 5


def test_exactly_one_machine_stays_up(fly: dict[str, Any]) -> None:
    service = fly["http_service"]
    assert service["auto_stop_machines"] == "off"
    assert service["auto_start_machines"] is False
    assert service["min_machines_running"] == 1
    assert service["force_https"] is True


def test_the_health_check_is_liveness_only(fly: dict[str, Any]) -> None:
    (check,) = fly["http_service"]["checks"]
    assert check["method"] == "GET"
    assert check["path"] == "/health"  # never /ready: a Neon outage must not restart the machine


def test_the_build_uses_the_backend_dockerfile_from_the_repo_root(fly: dict[str, Any]) -> None:
    assert (FLY_PATH.parent / fly["build"]["dockerfile"]).is_file()


def test_the_machine_is_small_and_in_ashburn(fly: dict[str, Any]) -> None:
    (vm,) = fly["vm"]
    assert vm == {"size": "shared-cpu-1x", "memory": "1gb"}
    assert fly["primary_region"] == "iad"


def test_env_holds_the_production_switches(fly: dict[str, Any]) -> None:
    env = fly["env"]
    assert env["APP_ENV"] == "production"
    assert env["NEON_CLIENT"] == "api"
    assert env["COMPANY_CONNECTION_PROFILE"] == "neon"


def test_public_base_url_is_valid_and_is_this_app(fly: dict[str, Any]) -> None:
    url = fly["env"]["PUBLIC_BASE_URL"]
    settings = Settings(_env_file=None, public_base_url=url)
    assert settings.public_host == fly["app"] + ".fly.dev"
    assert url.startswith("https://")


SECRET_NAME = re.compile(r"(?i)_URL$|KEY|SECRET|TOKEN|PASSWORD|MODEL|LLM_PRICES")


def test_env_holds_nothing_secret_and_no_model_setting(fly: dict[str, Any]) -> None:
    # Secrets and model settings are Fly secrets (D-058, D-255).
    for name, value in fly["env"].items():
        if name != "PUBLIC_BASE_URL":
            assert not SECRET_NAME.search(name), name
            assert "://" not in str(value), name
        assert "postgres" not in str(value).lower(), name
