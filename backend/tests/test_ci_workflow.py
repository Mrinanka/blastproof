"""Guards on the CI and Dependabot config (step 2: DW-B02, ARCH-21, SEC-21, TEST-01, D-072-D-075).

The green check itself is only visible on GitHub; these tests stop the workflow from quietly
losing a safety property (no secrets, pinned actions, integration tests actually running).
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_PATH = REPO_ROOT / ".github" / "dependabot.yml"

SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

# The commands each job must run, in this order (D-179). The `unit` job is the fast one: lint and
# the tests that need no Docker. The `integration` job runs everything, so plain `pytest` skipping
# the integration tests locally can never skip them in CI.
UNIT_RUN_ORDER = [
    "uv sync --locked",
    "uv run ruff check",
    "uv run ruff format --check",
    "uv run pytest",
]
INTEGRATION_RUN_ORDER = [
    "uv sync --locked",
    "docker compose up",
    "demo_company/seed.py --target local",  # step 3: builds company_template and company_main
    "uv run pytest --integration",
]


def load_yaml(text: str) -> dict[Any, Any]:
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data


def steps(workflow: dict[Any, Any], job: str | None = None) -> list[dict[str, Any]]:
    jobs = [workflow["jobs"][job]] if job else list(workflow["jobs"].values())
    return [step for j in jobs for step in j["steps"]]


def secret_problems(text: str) -> list[str]:
    # TEST-01: pull-request CI holds no secrets (and so can never leak one to a fork's code).
    return [f"uses a secret: {line.strip()}" for line in text.splitlines() if "secrets." in line]


def pin_problems(workflow: dict[Any, Any]) -> list[str]:
    # A tag can be moved to other code; a full commit SHA cannot (D-075).
    return [
        f"action not pinned to a commit SHA: {step['uses']}"
        for step in steps(workflow)
        if "uses" in step and not SHA_PIN.match(step["uses"])
    ]


def run_order_problems(workflow: dict[Any, Any], job: str, order: list[str]) -> list[str]:
    runs = [step["run"] for step in steps(workflow, job) if "run" in step]
    positions = []
    for command in order:
        found = [i for i, run in enumerate(runs) if command in run]
        if not found:
            return [f"missing step: {command}"]
        positions.append(found[0])
    if positions != sorted(positions):
        return [f"steps out of order: {order}"]
    return []


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ci(ci_text: str) -> dict[Any, Any]:
    return load_yaml(ci_text)


@pytest.fixture(scope="module")
def dependabot() -> dict[Any, Any]:
    return load_yaml(DEPENDABOT_PATH.read_text(encoding="utf-8"))


def test_runs_on_pull_requests_and_pushes_to_long_lived_branches(ci: dict[Any, Any]) -> None:
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = ci[True]
    assert "pull_request" in triggers
    assert set(triggers["push"]["branches"]) == {"develop", "main"}


def test_token_is_read_only_and_checkout_keeps_no_credentials(ci: dict[Any, Any]) -> None:
    assert ci["permissions"] == {"contents": "read"}
    checkouts = [s for s in steps(ci) if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == len(ci["jobs"])
    assert all(c["with"]["persist-credentials"] is False for c in checkouts)


def test_uses_no_secrets(ci_text: str) -> None:
    assert secret_problems(ci_text) == []


def test_actions_are_pinned_to_commit_shas(ci: dict[Any, Any]) -> None:
    assert pin_problems(ci) == []


def test_runner_image_is_pinned(ci: dict[Any, Any]) -> None:
    for job in ci["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04"


def test_tests_run_as_test_env_with_fake_neon(ci: dict[Any, Any]) -> None:
    assert ci["env"]["APP_ENV"] == "test"
    assert ci["env"]["NEON_CLIENT"] == "fake"


def test_jobs_are_unit_and_integration(ci: dict[Any, Any]) -> None:
    assert set(ci["jobs"]) == {"unit", "integration"}


def test_unit_job_runs_lint_then_fast_tests_without_docker(ci: dict[Any, Any]) -> None:
    assert run_order_problems(ci, "unit", UNIT_RUN_ORDER) == []
    runs = [step["run"] for step in steps(ci, "unit") if "run" in step]
    assert not any("docker" in run for run in runs)
    (pytest_run,) = [run for run in runs if "uv run pytest" in run]
    assert "--integration" not in pytest_run
    assert " -m " not in f" {pytest_run} "


def test_integration_job_runs_postgres_then_every_test(ci: dict[Any, Any]) -> None:
    assert run_order_problems(ci, "integration", INTEGRATION_RUN_ORDER) == []
    runs = [step["run"] for step in steps(ci, "integration") if "run" in step]
    (compose,) = [run for run in runs if "docker compose up" in run]
    assert "--wait" in compose
    (pytest_run,) = [run for run in runs if "uv run pytest" in run]
    # Plain pytest skips the integration tests (D-179), so CI must opt in. Never filter with -m.
    assert "--integration" in pytest_run
    assert "not integration" not in pytest_run
    assert " -m " not in f" {pytest_run} "


def test_uv_version_comes_from_an_exact_pin(ci: dict[Any, Any]) -> None:
    setup_uvs = [s for s in steps(ci) if s.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert len(setup_uvs) == len(ci["jobs"])
    assert all(s["with"]["version-file"] == "backend/pyproject.toml" for s in setup_uvs)
    pyproject = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert re.fullmatch(r"==\d+\.\d+\.\d+", pyproject["tool"]["uv"]["required-version"])


def test_dependabot_watches_every_pinned_ecosystem(dependabot: dict[Any, Any]) -> None:
    assert dependabot["version"] == 2
    updates = {u["package-ecosystem"]: u for u in dependabot["updates"]}
    assert set(updates) == {"uv", "github-actions", "docker-compose", "npm"}
    for update in updates.values():
        assert update["target-branch"] == "develop"
    uv_dir = REPO_ROOT / updates["uv"]["directory"].lstrip("/")
    compose_dir = REPO_ROOT / updates["docker-compose"]["directory"].lstrip("/")
    assert (uv_dir / "uv.lock").is_file()
    assert (compose_dir / "docker-compose.yml").is_file()
    npm_dir = REPO_ROOT / updates["npm"]["directory"].lstrip("/")
    assert (npm_dir / "package-lock.json").is_file()
    assert updates["npm"]["cooldown"]["default-days"] >= 7


@pytest.mark.parametrize(
    ("bad_yaml", "check"),
    [
        (
            "jobs: {b: {steps: [{run: 'echo ${{ secrets.NEON_API_KEY }}'}]}}",
            secret_problems,
        ),
        (
            "jobs: {b: {steps: [{uses: actions/checkout@v7}]}}",
            lambda text: pin_problems(load_yaml(text)),
        ),
        (
            "jobs: {unit: {steps: [{run: uv sync --locked}, {run: uv run ruff check .}]}}",
            lambda text: run_order_problems(load_yaml(text), "unit", UNIT_RUN_ORDER),
        ),
        (
            "jobs: {integration: {steps: ["
            "{run: uv sync --locked}, {run: uv run pytest --integration},"
            "{run: docker compose up -d --wait}]}}",
            lambda text: run_order_problems(load_yaml(text), "integration", INTEGRATION_RUN_ORDER),
        ),
        (
            # Plain pytest in the integration job would silently skip the integration tests.
            "jobs: {integration: {steps: ["
            "{run: uv sync --locked}, {run: docker compose up -d --wait},"
            "{run: uv run --project backend python demo_company/seed.py --target local},"
            "{run: uv run pytest}]}}",
            lambda text: run_order_problems(load_yaml(text), "integration", INTEGRATION_RUN_ORDER),
        ),
    ],
    ids=[
        "secret",
        "tag-pinned-action",
        "missing-pytest",
        "pytest-before-postgres",
        "integration-job-without-flag",
    ],
)
def test_checks_catch_a_bad_workflow(bad_yaml: str, check: Any) -> None:
    assert check(bad_yaml) != []


def test_seed_runs_from_the_repo_root_against_local_only(ci: dict[Any, Any]) -> None:
    (seed,) = [s for s in steps(ci) if "demo_company/seed.py" in s.get("run", "")]
    # The repo root has no pyproject.toml, so uv must be pointed at the backend project.
    assert "uv run --project backend" in seed["run"]
    assert seed["working-directory"] == "${{ github.workspace }}"
    assert "--target neon" not in seed["run"]  # CI never talks to Neon (TEST-04)


def test_lint_covers_demo_company(ci: dict[Any, Any]) -> None:
    runs = [s["run"] for s in steps(ci) if "uv run ruff" in s.get("run", "")]
    assert len(runs) == 2
    assert all("../demo_company" in run for run in runs)


def test_lint_covers_claude_hook(ci: dict[Any, Any]) -> None:
    runs = [s["run"] for s in steps(ci) if "uv run ruff" in s.get("run", "")]
    assert len(runs) == 2
    assert all("../claude_hook" in run for run in runs)


def test_migrations_run_after_postgres_and_before_pytest(ci: dict[Any, Any]) -> None:
    # Step 4 (OPS-01). Checked here rather than in INTEGRATION_RUN_ORDER, so the exact-message
    # check below keeps its meaning.
    runs = [s.get("run", "") for s in steps(ci, "integration")]
    migrate = [i for i, run in enumerate(runs) if "uv run alembic upgrade head" in run]
    assert len(migrate) == 1
    compose = next(i for i, run in enumerate(runs) if "docker compose up" in run)
    pytest_run = next(i for i, run in enumerate(runs) if "uv run pytest" in run)
    assert compose < migrate[0] < pytest_run
    (step,) = [s for s in steps(ci) if "uv run alembic upgrade head" in s.get("run", "")]
    # The local dev owner on the Compose database: CI holds no real credential (TEST-01).
    assert step["env"]["BLASTPROOF_MIGRATOR_URL"].startswith(
        "postgresql://blastproof_owner:blastproof_owner@localhost:5432/"
    )


def test_order_check_catches_a_seed_before_postgres() -> None:
    bad = load_yaml(
        "jobs: {integration: {steps: ["
        "{run: uv sync --locked},"
        "{run: uv run --project backend python demo_company/seed.py --target local},"
        "{run: docker compose up -d --wait}, {run: uv run pytest --integration}]}}"
    )
    assert run_order_problems(bad, "integration", INTEGRATION_RUN_ORDER) == [
        f"steps out of order: {INTEGRATION_RUN_ORDER}"
    ]


def test_unit_job_runs_the_rules_only_evals_after_pytest(ci: dict[Any, Any]) -> None:
    # EVAL-06, OPS-01: every pull request replays the incidents with the rules alone. No model, no
    # Docker, no secret; the job set stays {unit, integration} (above).
    assert run_order_problems(ci, "unit", ["uv run pytest", "evals.run --mode rules"]) == []
    (step,) = [s for s in steps(ci, "unit") if "evals.run" in s.get("run", "")]
    assert step["run"].strip() == "uv run python -m evals.run --mode rules"
    assert "env" not in step
