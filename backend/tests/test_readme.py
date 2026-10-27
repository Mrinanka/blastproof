"""The README and setup guide must not drift from the code (step 20, BE-124, no Docker)."""

import re

import pytest

from app import cli
from tests.conftest import REPO_ROOT

DOCS = [REPO_ROOT / "README.md", REPO_ROOT / "docs" / "setup.md"]
IDS = [d.name for d in DOCS]


def text_of(path: object) -> str:
    return path.read_text(encoding="utf-8")  # type: ignore[attr-defined]


def subcommands() -> set[str]:
    action = next(a for a in cli._parser()._actions if a.dest == "command")
    return set(action.choices)  # type: ignore[arg-type]


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_cli_command_named_is_real(doc: object) -> None:
    named = set(re.findall(r"app\.cli\s+([a-z][a-z-]+)", text_of(doc)))
    assert named, "the docs should show at least one CLI command"
    assert named <= subcommands(), named - subcommands()


def test_the_setup_path_uses_bootstrap_and_export_openapi() -> None:
    joined = "".join(text_of(d) for d in DOCS)
    assert {"bootstrap", "seed-demo-actions", "export-openapi"} <= set(
        re.findall(r"app\.cli\s+([a-z][a-z-]+)", joined)
    )


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_env_name_named_is_in_an_env_example(doc: object) -> None:
    known = set()
    for example in ("backend", "frontend"):
        body = (REPO_ROOT / example / ".env.example").read_text(encoding="utf-8")
        known |= set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{3,})=", body, flags=re.MULTILINE))
    named = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})(?:=[^`]*)?`", text_of(doc)))
    named -= {"NEON", "PATH"}
    assert named <= known, sorted(named - known)


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_repo_path_named_exists(doc: object) -> None:
    body = text_of(doc)
    paths = set(
        re.findall(r"`((?:backend|frontend|demo_company|claude_hook|docs)/[\w./-]+)`", body)
    )
    paths |= {
        m
        for m in re.findall(r"\]\(((?!http)[^)#]+)", body)
        if not m.startswith("<")  # markdown links
    }
    base = doc.parent  # type: ignore[attr-defined]
    for p in sorted(paths):
        target = (base / p) if p.startswith(("../", "./")) else None
        assert (target or REPO_ROOT / p).exists() or (base / p).exists(), p


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_the_minimum_claude_code_version_is_stated(doc: object) -> None:
    assert "v2.1.295" in text_of(doc)
