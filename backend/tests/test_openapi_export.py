"""The committed OpenAPI file and the `export-openapi` command (step 20, OPS-05, no Docker).

The committed-file test is the backend half of the contract check: a route change without a
re-export fails here. The frontend job regenerates its types from the same file and fails on a diff.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.config import Settings
from app.main import create_app
from app.services.openapi_export import openapi_document
from tests.conftest import BACKEND_DIR, NetworkGuard

COMMITTED = BACKEND_DIR / "openapi.json"


def test_the_export_equals_what_the_running_app_serves() -> None:
    with TestClient(create_app(Settings(_env_file=None, app_env="test"))) as client:
        served = client.get("/openapi.json").json()
    assert json.loads(openapi_document()) == served


def test_the_export_is_deterministic_json_with_lf_line_endings() -> None:
    first, second = openapi_document(), openapi_document()
    assert first == second
    assert first.endswith("}\n") and "\r" not in first
    assert first.startswith('{\n  "openapi"')


def test_the_environment_and_dotenv_do_not_change_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = openapi_document()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("APP_ENV=production\nPUBLIC_BASE_URL=https://evil.example\n")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("BLASTPROOF_DATABASE_URL", "postgresql://u:p@nowhere/db")
    assert openapi_document() == baseline


def test_the_committed_file_equals_the_export() -> None:
    assert COMMITTED.read_bytes().decode("utf-8") == openapi_document(), (
        "backend/openapi.json is out of date: run `uv run python -m app.cli export-openapi` "
        "and then `npm run gen:api` in frontend/."
    )


def test_the_command_needs_no_database_and_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_network: NetworkGuard, capsys
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("BLASTPROOF_DATABASE_URL", raising=False)
    out = tmp_path / "api.json"
    assert cli.main(["export-openapi", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == openapi_document()
    assert capsys.readouterr().out == "Wrote api.json.\n"
    assert no_network.blocked == []


def test_an_unwritable_path_is_one_line(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["export-openapi", "--out", str(tmp_path / "missing" / "api.json")]) == 1
    assert capsys.readouterr().err == "Error: The OpenAPI file could not be written.\n"
