"""The eval runner's command line (backend plan §14): argument errors and refusals."""

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from evals import live_mode, run
from evals.live_mode import LiveModeError, check_live_settings

LOCAL_URLS: dict[str, Any] = {
    "blastproof_database_url": "postgresql+asyncpg://u:p@localhost:5432/blastproof",
    "checkpointer_database_url": "postgresql://blastproof_app:p@localhost:5432/blastproof",
    "local_company_reader_url": "postgresql://r:p@localhost:5433/company_main",
    "local_company_writer_url": "postgresql://w:p@localhost:5433/company_main",
    "fake_neon_admin_url": "postgresql://c:p@localhost:5433/postgres",
}


def local_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "company_connection_profile": "local",
        "neon_client": "fake",
        **LOCAL_URLS,
    } | overrides
    return Settings(_env_file=None, **values)


@pytest.fixture
def no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the live eval connected")

    monkeypatch.setattr(live_mode, "run_live", boom)
    monkeypatch.setattr(live_mode, "measure_reads", boom)
    monkeypatch.setattr(live_mode, "live_pipeline", boom)


def test_an_unknown_mode_is_an_argument_error() -> None:
    with pytest.raises(SystemExit) as exit_:
        run.main(["--mode", "nightly"])
    assert exit_.value.code == 2


def test_langsmith_without_live_mode_is_refused() -> None:
    with pytest.raises(SystemExit) as exit_:
        run.main(["--langsmith"])
    assert exit_.value.code == 2


def test_the_default_output_is_under_evals_results() -> None:
    args = run.build_parser().parse_args([])
    assert args.out is None
    assert args.mode == "rules"
    assert Path(run.__file__).resolve().parent / "results" == run.RESULTS_DIR


def test_live_mode_with_production_is_refused_before_anything_connects(
    monkeypatch: pytest.MonkeyPatch, no_connection: None, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "Settings", lambda: Settings.model_construct(app_env="production"))
    assert run.main(["--mode", "live", "--out", "unused.json"]) == 2
    assert "APP_ENV=production" in capsys.readouterr().err


def test_live_mode_with_invalid_settings_names_the_fields_only(
    monkeypatch: pytest.MonkeyPatch, no_connection: None, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "bogus-value-that-must-not-print")
    assert run.main(["--mode", "live"]) == 2
    err = capsys.readouterr().err
    assert "settings are invalid: app_env" in err
    assert "bogus-value" not in err


def test_langsmith_without_a_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, no_connection: None, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "Settings", local_settings)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert run.main(["--mode", "live", "--langsmith"]) == 2
    assert "LANGSMITH_API_KEY" in capsys.readouterr().err


def test_the_local_settings_pass() -> None:
    check_live_settings(local_settings())


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings.model_construct(app_env="production"), "APP_ENV=production"),
        (local_settings(company_connection_profile="neon"), "local profile"),
        (local_settings(neon_client="api"), "fake Neon client"),
        (local_settings(local_company_writer_url=None), "LOCAL_COMPANY_WRITER_URL"),
        (local_settings(fake_neon_admin_url=None), "FAKE_NEON_ADMIN_URL"),
        (local_settings(checkpointer_database_url=None), "CHECKPOINTER_DATABASE_URL"),
        (local_settings(blastproof_database_url=None), "BLASTPROOF_DATABASE_URL"),
    ],
    ids=[
        "production",
        "neon-profile",
        "real-neon-client",
        "no-writer",
        "no-admin",
        "no-checkpointer",
        "no-app-db",
    ],
)
def test_live_settings_refusals(settings: Settings, message: str) -> None:
    with pytest.raises(LiveModeError, match=message):
        check_live_settings(settings)
    # The message holds a name, never a URL.
    try:
        check_live_settings(settings)
    except LiveModeError as exc:
        assert "postgresql" not in str(exc)


def test_langsmith_runs_the_experiments_after_the_live_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    no_connection: None,
) -> None:
    from evals import langsmith_experiments
    from evals.langsmith_experiments import ExperimentRun
    from evals.metrics import Outcome

    seen: dict[str, Any] = {}

    async def fake_live(*args: Any, **kwargs: Any) -> list[Outcome]:
        return [Outcome("x", "labeled.yaml", "sql", "review", "review", reason="r")]

    async def no_reads(*args: Any, **kwargs: Any) -> list[Outcome]:
        return []

    async def fake_experiments(client: Any, aevaluate: Any, **kwargs: Any) -> list[ExperimentRun]:
        seen.update(kwargs)
        return [
            ExperimentRun("rules-only", "rules-only-1"),
            ExperimentRun("rules+judge", None, "X"),
        ]

    monkeypatch.setattr(run, "Settings", local_settings)
    monkeypatch.setattr(live_mode, "run_live", fake_live)
    monkeypatch.setattr(live_mode, "measure_reads", no_reads)
    monkeypatch.setattr(langsmith_experiments, "run_experiments", fake_experiments)
    monkeypatch.setattr("langsmith.Client", lambda: object())
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key-value-9999")
    code = run.main(["--mode", "live", "--langsmith", "--out", str(tmp_path / "o.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert seen["labeled_path"] == run.LABELED
    assert "LangSmith rules-only: rules-only-1" in out
    assert "LangSmith rules+judge: FAILED: X" in out
    assert "9999" not in out
