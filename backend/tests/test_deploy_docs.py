"""Guards on docs/deploy.md (Deploy: OPS-13, SEC-19). Its secrets table is machine-checked."""

import re

import pytest

import app.main as main
from app.cli import _parser
from app.config import SET_EXPLICITLY_IN_PRODUCTION, Settings
from tests.test_dockerfile import REPO_ROOT
from tests.test_production_neon_settings import COMPLETE

DEPLOY_MD = (REPO_ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")

# What `_check_production_settings` requires, as env names (test_production_neon_settings.COMPLETE
# is a complete production config; app_env and neon_client are fly.toml's, not secrets).
REQUIRED = {name.upper() for name in COMPLETE if name not in ("app_env", "neon_client")} | {
    "BLASTPROOF_MIGRATOR_URL"
}
OPTIONAL = {
    "SUMMARY_MODEL_OPTIONS",
    "JUDGE_MODEL_OPTIONS",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
}


def secrets_table() -> dict[str, str]:
    """Secret name -> the 'Required' cell, from the first table that starts with | Secret |."""
    rows = {}
    in_table = False
    for line in DEPLOY_MD.splitlines():
        if line.startswith("| Secret |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set(cells[0]) <= {"-"}:
                continue
            rows[cells[0].strip("`")] = cells[1]
    return rows


def test_the_table_lists_exactly_the_required_and_optional_secrets() -> None:
    table = secrets_table()
    assert {name for name, required in table.items() if required == "yes"} == REQUIRED
    assert {name for name, required in table.items() if required == "no"} == OPTIONAL


def test_the_required_list_covers_what_production_checks() -> None:
    assert {env for env in SET_EXPLICITLY_IN_PRODUCTION.values()} <= REQUIRED
    assert not any(
        name.startswith(("LOCAL_", "SEED_")) or name == "FAKE_NEON_ADMIN_URL"
        for name in secrets_table()
    )


def test_the_guide_never_tells_you_to_use_backend_env_for_production() -> None:
    for line in DEPLOY_MD.splitlines():
        if "backend/.env" in line:
            assert re.search(r"(?i)\bnever\b", line), line


def test_the_commands_it_names_are_real() -> None:
    commands = _parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    for name in ("deploy-check", "bootstrap", "preview-sql"):
        assert name in commands
        assert f"app.cli {name}" in DEPLOY_MD
    assert "--ha=false" in DEPLOY_MD
    assert "fly scale count 1" in DEPLOY_MD
    assert "fly secrets import" in DEPLOY_MD
    assert (REPO_ROOT / "deploy" / "neon" / "blastproof-app.sql").is_file()
    assert (REPO_ROOT / "claude_hook" / "workspace.py").is_file()
    assert (REPO_ROOT / "demo_company" / "rebase_dates.py").is_file()


def test_the_guide_has_no_secret_looking_value() -> None:
    assert not re.search(r"postgres(ql)?(\+asyncpg)?://[^\s:]+:(?!\.\.\.)[^\s@]+@", DEPLOY_MD)
    assert not re.search(r"bp_[A-Za-z0-9_-]{43}|npg_[A-Za-z0-9]{8,}", DEPLOY_MD)


def production(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(COMPLETE | overrides))  # type: ignore[arg-type]


def test_a_production_config_built_from_the_documented_names_passes() -> None:
    main._check_production_settings(production())


@pytest.mark.parametrize("env_name", sorted(REQUIRED - {"BLASTPROOF_MIGRATOR_URL"}))
def test_production_fails_without_each_required_secret(env_name: str) -> None:
    field = env_name.lower()
    unset = "" if env_name in SET_EXPLICITLY_IN_PRODUCTION.values() else None  # "" = the default
    with pytest.raises(main.MissingProductionSettingError, match=env_name):
        main._check_production_settings(production(**{field: unset}))
