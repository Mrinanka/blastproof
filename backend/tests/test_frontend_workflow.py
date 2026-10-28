"""Guards on the frontend CI workflow (frontend step F1, OPS-03).

Reuses the checks from test_ci_workflow.py. The green check itself is only visible on GitHub.
"""

import json
from typing import Any

import pytest

from tests.test_ci_workflow import (
    REPO_ROOT,
    load_yaml,
    pin_problems,
    run_order_problems,
    secret_problems,
    steps,
)

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "frontend.yml"
FRONTEND_RUN_ORDER = [
    "npm ci",
    "npm run lint",
    "npm run format:check",
    "npm run typecheck",
    "npm test",
    "npm run build",
]


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(text: str) -> dict[Any, Any]:
    return load_yaml(text)


def test_runs_on_pull_requests_and_pushes_to_long_lived_branches(workflow: dict[Any, Any]) -> None:
    triggers = workflow[True]  # PyYAML reads the bare key `on` as True.
    assert "pull_request" in triggers
    assert not (triggers["pull_request"] or {}).get("paths")  # no path filter (FE section 13)
    assert set(triggers["push"]["branches"]) == {"develop", "main"}


def test_token_is_read_only_and_checkout_keeps_no_credentials(workflow: dict[Any, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    checkouts = [s for s in steps(workflow) if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_uses_no_secrets(text: str) -> None:
    assert secret_problems(text) == []


def test_actions_are_pinned_to_commit_shas(workflow: dict[Any, Any]) -> None:
    assert pin_problems(workflow) == []


def test_runner_image_is_pinned_and_runs_in_the_frontend_folder(workflow: dict[Any, Any]) -> None:
    (job,) = workflow["jobs"].values()
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["defaults"]["run"]["working-directory"] == "frontend"


def test_node_comes_from_nvmrc_and_npm_is_cached(workflow: dict[Any, Any]) -> None:
    (setup,) = [s for s in steps(workflow) if s.get("uses", "").startswith("actions/setup-node@")]
    assert setup["with"]["node-version-file"] == "frontend/.nvmrc"
    assert setup["with"]["cache"] == "npm"
    assert setup["with"]["cache-dependency-path"] == "frontend/package-lock.json"
    assert (REPO_ROOT / "frontend" / ".nvmrc").is_file()
    assert (REPO_ROOT / "frontend" / "package-lock.json").is_file()


def test_steps_run_in_order(workflow: dict[Any, Any]) -> None:
    (job,) = workflow["jobs"]
    assert run_order_problems(workflow, job, FRONTEND_RUN_ORDER) == []


def test_generated_types_are_checked_against_the_committed_schema(
    workflow: dict[Any, Any],
) -> None:
    (job,) = workflow["jobs"]
    order = ["npm ci", "npm run gen:api", "git diff --exit-code src/lib/api/schema.d.ts"]
    assert run_order_problems(workflow, job, order) == []
    runs = [s["run"] for s in steps(workflow) if "run" in s]
    assert runs.index("npm run gen:api") < runs.index("npm run lint")


def test_gen_api_reads_the_committed_file_not_a_running_server() -> None:
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    script = package["scripts"]["gen:api"]
    assert "../backend/openapi.json" in script
    assert "http" not in script
    assert (REPO_ROOT / "backend" / "openapi.json").is_file()


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "jobs: {frontend: {steps: [{run: npm ci}, {run: npm run lint}]}}",
        "jobs: {frontend: {steps: [{run: npm run build}, {run: npm ci}, {run: npm run lint},"
        "{run: npm run format:check}, {run: npm run typecheck}, {run: npm test}]}}",
    ],
    ids=["missing-steps", "out-of-order"],
)
def test_order_check_catches_a_bad_workflow(bad_yaml: str) -> None:
    assert run_order_problems(load_yaml(bad_yaml), "frontend", FRONTEND_RUN_ORDER) != []
