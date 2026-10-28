"""Guards on .github/workflows/deploy.yml and image.yml (Deploy: OPS-12, TEST-01, D-261).

The deploy job holds the one production secret, so these tests pin everything around it: the only
triggers, the branch check, where the secret may appear, SHA-pinned actions and an exact flyctl.
"""

import re
from typing import Any

import pytest

from tests.test_ci_workflow import REPO_ROOT, load_yaml, steps

DEPLOY_PATH = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
IMAGE_PATH = REPO_ROOT / ".github" / "workflows" / "image.yml"

# Unlike test_ci_workflow's pattern, this allows a sub-path (superfly/flyctl-actions/setup-flyctl).
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def pin_problems(workflow: dict[Any, Any]) -> list[str]:
    return [
        f"action not pinned to a commit SHA: {step['uses']}"
        for step in steps(workflow)
        if "uses" in step and not SHA_PIN.match(step["uses"])
    ]


def deploy_problems(text: str) -> list[str]:
    workflow = load_yaml(text)
    problems = pin_problems(workflow)
    triggers = workflow[True]  # PyYAML reads the bare key `on` as True
    if set(triggers) != {"push", "workflow_dispatch"} or triggers["push"]["branches"] != [
        "release"
    ]:
        problems.append("triggers are not exactly push to release and workflow_dispatch")
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("token is not read-only")
    concurrency = workflow.get("concurrency", {})
    if not concurrency.get("group") or concurrency.get("cancel-in-progress") is not False:
        problems.append("a running deploy can be cancelled")
    (job,) = workflow["jobs"].values()
    if "refs/heads/release" not in job.get("if", ""):
        problems.append("the job does not check the branch")
    if not job.get("environment"):
        problems.append("no environment")
    if not job.get("timeout-minutes"):
        problems.append("no timeout")
    if job.get("runs-on") != "ubuntu-24.04":
        problems.append("runner not pinned")
    secret_lines = [line for line in text.splitlines() if "secrets." in line]
    if [line.strip() for line in secret_lines if "secrets.FLY_API_TOKEN" not in line]:
        problems.append("uses a secret other than FLY_API_TOKEN")
    token_steps = [s for s in job["steps"] if "secrets.FLY_API_TOKEN" in str(s)]
    deploy = [s for s in job["steps"] if "flyctl deploy" in s.get("run", "")]
    if len(deploy) != 1 or token_steps != deploy:
        problems.append("FLY_API_TOKEN is not in the deploy step alone")
    elif deploy[0].get("env") != {"FLY_API_TOKEN": "${{ secrets.FLY_API_TOKEN }}"}:
        problems.append("FLY_API_TOKEN is not in the deploy step's env")
    elif "secrets." in deploy[0]["run"]:
        problems.append("the token is written into the command line")
    elif "--remote-only" not in deploy[0]["run"] or "--ha=false" not in deploy[0]["run"]:
        problems.append("deploy lacks --remote-only or --ha=false")
    (setup,) = [s for s in job["steps"] if s.get("uses", "").startswith("superfly/flyctl-actions/")]
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(setup.get("with", {}).get("version", ""))):
        problems.append("flyctl version is not exact")
    for checkout in [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")]:
        if checkout.get("with", {}).get("persist-credentials") is not False:
            problems.append("checkout keeps credentials")
    return problems


@pytest.fixture(scope="module")
def deploy_text() -> str:
    return DEPLOY_PATH.read_text(encoding="utf-8")


def test_the_deploy_workflow_has_no_problems(deploy_text: str) -> None:
    assert deploy_problems(deploy_text) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "  push:\n    branches: [release]\n",
            "  pull_request:\n  push:\n    branches: [release]\n",
        ),
        ("    branches: [release]", "    branches: [release, develop]"),
        ("run: flyctl deploy --remote-only --ha=false", "run: echo ${{ secrets.OTHER }}"),
        (
            "run: flyctl deploy --remote-only --ha=false\n",
            "run: flyctl deploy --remote-only --ha=false ${{ secrets.FLY_API_TOKEN }}\n",
        ),
        ("setup-flyctl@fc53c09e1bc3be6f54706524e3b82c4f462f77be", "setup-flyctl@v1.5"),
        ("--remote-only --ha=false", "--remote-only"),
        ("cancel-in-progress: false", "cancel-in-progress: true"),
        ("    if: github.ref == 'refs/heads/release'\n", ""),
        ("    environment: production\n", ""),
        ("    timeout-minutes: 20\n", ""),
        ("version: 0.4.123", "version: latest"),
        ("contents: read", "contents: write"),
    ],
    ids=[
        "pull-request-trigger",
        "second-branch",
        "inline-other-secret",
        "token-outside-env",
        "tag-pinned-action",
        "ha-missing",
        "cancel-in-progress",
        "no-branch-check",
        "no-environment",
        "no-timeout",
        "flyctl-latest",
        "write-token",
    ],
)
def test_the_checks_catch_a_bad_deploy_workflow(deploy_text: str, old: str, new: str) -> None:
    assert old in deploy_text, "the case no longer matches deploy.yml"
    assert deploy_problems(deploy_text.replace(old, new, 1)) != []


# --- image.yml: builds the image and smoke-tests it; no secrets -------------------------------


@pytest.fixture(scope="module")
def image_text() -> str:
    return IMAGE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def image(image_text: str) -> dict[Any, Any]:
    return load_yaml(image_text)


def test_the_image_workflow_uses_no_secrets(image_text: str) -> None:
    assert "secrets." not in image_text


def test_the_image_workflow_is_read_only_and_pinned(image: dict[Any, Any]) -> None:
    assert image["permissions"] == {"contents": "read"}
    assert pin_problems(image) == []
    for checkout in [s for s in steps(image) if s.get("uses", "").startswith("actions/checkout@")]:
        assert checkout["with"]["persist-credentials"] is False


def test_the_image_workflow_runs_on_pull_requests_and_filters_paths(image: dict[Any, Any]) -> None:
    triggers = image[True]
    assert "pull_request" in triggers
    assert set(triggers["push"]["branches"]) == {"develop", "release"}
    for paths in (triggers["pull_request"]["paths"], triggers["push"]["paths"]):
        assert {"backend/**", "demo_company/schema_docs.md", ".dockerignore", "fly.toml"} <= set(
            paths
        )


def test_the_image_is_built_before_it_is_smoke_tested(image: dict[Any, Any]) -> None:
    runs = [s["run"] for s in steps(image) if "run" in s]
    build = next(i for i, run in enumerate(runs) if "docker build -f backend/Dockerfile" in run)
    smoke = next(i for i, run in enumerate(runs) if "docker/image-smoke.sh" in run)
    assert build < smoke


def test_the_smoke_script_exists_and_checks_the_promised_things() -> None:
    script = (REPO_ROOT / "docker" / "image-smoke.sh").read_text(encoding="utf-8")
    for needle in (
        "id",
        "--network none",
        "/health",
        "refusing to start",
        "alembic upgrade head --sql",
    ):
        assert needle in script
