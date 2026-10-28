"""Guards on the live-evals workflow (backend plan §14, EVAL-09, OPS-02).

It holds model and LangSmith keys, so it must never run for a pull request, and the keys must reach
only the step that needs them. Same pattern as `test_ci_workflow.py`.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_ci_workflow import REPO_ROOT, load_yaml, pin_problems, run_order_problems, steps

EVALS_PATH = REPO_ROOT / ".github" / "workflows" / "evals.yml"
ALLOWED_SECRETS = {"GOOGLE_API_KEY", "GROQ_API_KEY", "LANGSMITH_API_KEY", "SUMMARY_MODEL_OPTIONS"}
FORBIDDEN_TRIGGERS = {"pull_request", "pull_request_target", "push", "workflow_run"}
# SUMMARY_MODEL_OPTIONS is not here: it can hold a provider key, so it is a secret.
MODEL_SETTINGS = {
    "SUMMARY_MODEL",
    "JUDGE_MODEL",
    "JUDGE_MODEL_OPTIONS",
    "LLM_PRICES",
}
RUN_ORDER = [
    "docker compose up",
    "demo_company/seed.py --target local",
    "uv run alembic upgrade head",
    "app.cli knowledge-scan",
    "app.cli knowledge-ingest",
    "evals.run --mode live",
]


def trigger_problems(workflow: dict[Any, Any]) -> list[str]:
    triggers = workflow[True]  # PyYAML reads the bare key `on` as the boolean True
    names = set(triggers) if isinstance(triggers, dict) else set(triggers)
    problems = [f"runs on {name}" for name in sorted(names & FORBIDDEN_TRIGGERS)]
    if names != {"schedule", "workflow_dispatch"}:
        problems.append(f"triggers are {sorted(names)}, not schedule and workflow_dispatch")
    return problems


def secret_problems(text: str) -> list[str]:
    used = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    return [f"uses secret {name}" for name in sorted(used - ALLOWED_SECRETS)]


def secret_placement_problems(workflow: dict[Any, Any]) -> list[str]:
    """A secret may appear only in a step's own `env`, never workflow-wide or inside a script."""
    problems = []
    if "secrets." in str(workflow.get("env", {})):
        problems.append("a secret is in the workflow-level env")
    for job in workflow["jobs"].values():
        if "secrets." in str(job.get("env", {})):
            problems.append("a secret is in a job-level env")
    for step in steps(workflow):
        if "secrets." in step.get("run", ""):
            problems.append(f"a secret is inline in a script: {step['run'][:40]}")
    return problems


def model_variable_problems(workflow: dict[Any, Any]) -> list[str]:
    env = workflow.get("env", {})
    return [
        f"{name} is not a repository variable"
        for name in sorted(MODEL_SETTINGS)
        if not str(env.get(name, "")).startswith("${{ vars.")
    ]


@pytest.fixture(scope="module")
def text() -> str:
    return EVALS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def evals(text: str) -> dict[Any, Any]:
    return load_yaml(text)


def test_triggers_are_schedule_and_manual_only(evals: dict[Any, Any]) -> None:
    assert trigger_problems(evals) == []
    assert evals[True]["schedule"][0]["cron"]


def test_the_token_is_read_only_and_checkout_keeps_no_credentials(evals: dict[Any, Any]) -> None:
    assert evals["permissions"] == {"contents": "read"}
    checkouts = [s for s in steps(evals) if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_actions_are_pinned_to_commit_shas(evals: dict[Any, Any]) -> None:
    assert pin_problems(evals) == []
    uses = {s["uses"].split("@")[0] for s in steps(evals) if "uses" in s}
    assert "actions/upload-artifact" in uses


def test_only_the_allowed_secrets_are_used(text: str) -> None:
    assert secret_problems(text) == []
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == ALLOWED_SECRETS


def test_secrets_reach_only_the_eval_step(evals: dict[Any, Any]) -> None:
    assert secret_placement_problems(evals) == []
    holders = [s for s in steps(evals) if "secrets." in str(s.get("env", {}))]
    assert len(holders) == 1
    assert "evals.run --mode live" in holders[0]["run"]


def test_the_summary_options_come_from_a_secret_in_the_eval_step_only(
    evals: dict[Any, Any],
) -> None:
    # They can hold an api_key (OpenRouter), so never a variable and never workflow-wide.
    assert "SUMMARY_MODEL_OPTIONS" not in evals["env"]
    (step,) = [s for s in steps(evals) if "evals.run --mode live" in s.get("run", "")]
    assert step["env"]["SUMMARY_MODEL_OPTIONS"] == "${{ secrets.SUMMARY_MODEL_OPTIONS }}"


def test_it_runs_as_test_env_on_the_local_profile_with_fake_neon(evals: dict[Any, Any]) -> None:
    env = evals["env"]
    assert env["APP_ENV"] == "test"
    assert env["NEON_CLIENT"] == "fake"
    assert env["COMPANY_CONNECTION_PROFILE"] == "local"
    # Local Compose values only: nothing in the env points at Neon.
    assert not any("neon" in str(v).lower() for k, v in env.items() if k.endswith("_URL"))
    assert "NEON_API_KEY" not in env


def test_the_steps_run_in_order(evals: dict[Any, Any]) -> None:
    assert run_order_problems(evals, "evals", RUN_ORDER) == []


def test_postgres_is_waited_for_and_logs_are_kept_on_failure(evals: dict[Any, Any]) -> None:
    all_steps = steps(evals, "evals")
    (compose,) = [s for s in all_steps if "docker compose up" in s.get("run", "")]
    assert "--wait" in compose["run"]
    logs = [s for s in all_steps if "docker compose logs" in s.get("run", "")]
    assert [s.get("if") for s in logs] == ["failure()"]


def test_results_are_uploaded_even_when_the_run_fails(evals: dict[Any, Any]) -> None:
    (upload,) = [
        s for s in steps(evals) if s.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "backend/evals/results/live.json"
    runs = [s.get("run", "") for s in steps(evals, "evals")]
    eval_step = next(i for i, run in enumerate(runs) if "evals.run --mode live" in run)
    upload_step = next(
        i for i, s in enumerate(steps(evals, "evals")) if "upload-artifact" in s.get("uses", "")
    )
    assert eval_step < upload_step


def test_langsmith_experiments_run_only_when_the_key_is_set(evals: dict[Any, Any]) -> None:
    (step,) = [s for s in steps(evals) if "evals.run --mode live" in s.get("run", "")]
    assert '[ -n "$LANGSMITH_API_KEY" ]' in step["run"]
    assert "--langsmith" in step["run"]


def test_model_names_options_and_prices_come_from_repository_variables(
    evals: dict[Any, Any],
) -> None:
    assert model_variable_problems(evals) == []
    assert not re.search(
        r"(?:google_genai|anthropic|openai):[\w.-]+", EVALS_PATH.read_text("utf-8")
    )


def test_the_job_has_a_timeout(evals: dict[Any, Any]) -> None:
    assert 0 < evals["jobs"]["evals"]["timeout-minutes"] <= 60


@pytest.mark.parametrize(
    ("bad_yaml", "check"),
    [
        ("on: {pull_request: {}, schedule: [{cron: '0 0 * * *'}]}", trigger_problems),
        ("on: {schedule: [{cron: '0 0 * * *'}]}", trigger_problems),
        ("on: {workflow_dispatch: {}, push: {}}", trigger_problems),
        (
            "jobs: {b: {steps: [{run: 'echo ${{ secrets.NEON_API_KEY }}'}]}}",
            lambda t: secret_problems(t),
        ),
        (
            "env: {X: '${{ secrets.GOOGLE_API_KEY }}'}\njobs: {b: {steps: [{run: ls}]}}",
            lambda t: secret_placement_problems(load_yaml(t)),
        ),
        (
            "jobs: {b: {steps: [{run: 'echo ${{ secrets.GOOGLE_API_KEY }}'}]}}",
            lambda t: secret_placement_problems(load_yaml(t)),
        ),
        (
            "jobs: {b: {steps: [{uses: actions/upload-artifact@v7}]}}",
            lambda t: pin_problems(load_yaml(t)),
        ),
        (
            "env: {SUMMARY_MODEL: 'google_genai:gemini-3.8-flash'}",
            lambda t: model_variable_problems(load_yaml(t)),
        ),
        (
            "jobs: {evals: {steps: [{run: uv run python -m evals.run --mode live},"
            "{run: docker compose up -d --wait}]}}",
            lambda t: run_order_problems(load_yaml(t), "evals", RUN_ORDER),
        ),
    ],
    ids=[
        "pull-request-trigger",
        "no-manual-trigger",
        "push-trigger",
        "unlisted-secret",
        "workflow-wide-secret",
        "inline-secret",
        "tag-pinned-upload",
        "literal-model-name",
        "eval-before-postgres",
    ],
)
def test_the_checks_catch_a_bad_workflow(bad_yaml: str, check: Any) -> None:
    assert check(bad_yaml) != []


def test_the_workflow_file_exists_next_to_ci() -> None:
    assert Path(EVALS_PATH).is_file()
    assert (EVALS_PATH.parent / "ci.yml").is_file()
