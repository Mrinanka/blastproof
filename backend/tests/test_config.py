"""BE-125, BE-126: settings come from the environment, with backend plan §11's defaults."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_plan_section_11(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=None)
    assert s.app_env == "local"
    assert s.review_wait_seconds == 20
    assert s.status_max_wait_seconds == 50
    assert s.review_timeout_hours == 24
    assert s.agent_poll_budget_minutes == 10
    assert s.max_concurrent_shadows == 2
    assert s.llm_timeout_seconds == 20
    assert s.llm_max_tokens == 1024
    assert s.max_writes_per_minute_per_agent == 30
    assert s.max_llm_cost_per_action_usd == 0.10
    assert s.judge_min_confidence == 0.6
    assert s.retention_days == 30


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REVIEW_WAIT_SECONDS", "5")
    s = Settings(_env_file=None)
    assert s.app_env == "production"
    assert s.review_wait_seconds == 5


def test_unknown_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unknown_keys_in_env_file_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=test\nSOME_KEY_A_LATER_STEP_ADDS=x\n", encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.app_env == "test"


def test_env_file_none_never_reads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A .env in the working directory must be ignored when _env_file=None.
    monkeypatch.delenv("APP_ENV", raising=False)
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).app_env == "local"
    # Control: the default env_file does read it, so the check above means something.
    assert Settings().app_env == "production"


def test_log_level_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings(_env_file=None).log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_migrator_url_is_optional_and_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLASTPROOF_MIGRATOR_URL", raising=False)
    assert Settings(_env_file=None).blastproof_migrator_url is None

    url = "postgresql://blastproof_owner:s3cret-value@localhost:5432/blastproof"
    monkeypatch.setenv("BLASTPROOF_MIGRATOR_URL", url)
    s = Settings(_env_file=None)
    assert s.blastproof_migrator_url is not None
    assert s.blastproof_migrator_url.get_secret_value() == url
    # A printed or logged Settings object never shows the password.
    assert "s3cret-value" not in repr(s)
    assert "s3cret-value" not in str(s.model_dump())


def test_database_url_and_jwt_secret_are_optional_and_never_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BLASTPROOF_DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    s = Settings(_env_file=None)
    assert s.blastproof_database_url is None
    assert s.jwt_secret is None

    url = "postgresql+asyncpg://blastproof_app:db-pass-value@localhost:5432/blastproof"
    secret = "jwt-secret-value-" + "x" * 32
    monkeypatch.setenv("BLASTPROOF_DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET", secret)
    s = Settings(_env_file=None)
    assert s.blastproof_database_url is not None
    assert s.blastproof_database_url.get_secret_value() == url
    assert s.jwt_secret is not None
    assert s.jwt_secret.get_secret_value() == secret
    for hidden in ("db-pass-value", "jwt-secret-value"):
        assert hidden not in repr(s)
        assert hidden not in str(s.model_dump())


def test_short_jwt_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "x" * 31)
    with pytest.raises(ValidationError, match="32 bytes"):
        Settings(_env_file=None)


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # backend/.env holds empty placeholders until the owner fills them in.
    monkeypatch.setenv("BLASTPROOF_DATABASE_URL", "")
    monkeypatch.setenv("JWT_SECRET", "")
    s = Settings(_env_file=None)
    assert s.blastproof_database_url is None
    assert s.jwt_secret is None
