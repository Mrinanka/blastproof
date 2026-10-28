"""Guards on .dockerignore (Deploy: SEC-19). It is an allowlist: `*`, then what the image needs.

Docker's matching is simulated here (last matching rule wins; `*` stays inside one path segment;
a rule also matches everything below a directory it names), so a re-included folder that the
Dockerfile COPYs can never be quietly dropped, and `.env`, tests and evals can never get in.
"""

import re

import pytest

from tests.test_dockerfile import REPO_ROOT, copy_sources

IGNORE = REPO_ROOT / ".dockerignore"


def rule_lines() -> list[str]:
    lines = (line.strip() for line in IGNORE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def to_regex(pattern: str) -> re.Pattern[str]:
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{regex}$")


def in_context(path: str) -> bool:
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    included = True
    for line in rule_lines():
        negated = line.startswith("!")
        pattern = to_regex(line.removeprefix("!").strip("/"))
        if any(pattern.match(prefix) for prefix in prefixes):
            included = negated
    return included


def test_the_file_starts_by_excluding_everything() -> None:
    assert rule_lines()[0] == "*"


def test_the_dockerfile_and_everything_it_copies_are_in_the_context() -> None:
    assert in_context("backend/Dockerfile")
    sources = copy_sources(from_stage=False)
    assert sources
    for source in sources:
        path = REPO_ROOT / source
        assert path.exists(), source
        files = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path]
        for file in files:
            relative = file.relative_to(REPO_ROOT).as_posix()
            if "__pycache__" in relative or relative.endswith(".pyc"):
                continue
            assert in_context(relative), relative


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "backend/.env",
        "backend/.env.example",
        "backend/app/.env",
        "backend/tests/test_main.py",
        "backend/evals/run.py",
        "backend/.venv/bin/python",
        "backend/.cache/fastembed/model.onnx",
        "backend/app/__pycache__/main.pyc",
        "frontend/package.json",
        "claude_hook/workspace.py",
        "demo_company/seed.py",
        "docs/deploy.md",
        "deploy/neon/blastproof-app.sql",
    ],
)
def test_these_never_enter_the_context(path: str) -> None:
    assert not in_context(path)
