"""Guards on backend/Dockerfile (Deploy, backend plan §15: OPS-07, SEC-19, ARCH-05, BE-86).

The image itself is built and smoke-tested by docker/image-smoke.sh (CI: image.yml). These tests
read the file, so a quiet edit cannot lose a safety property: pinned bases, a non-root user, the
model downloaded at build, a graceful shutdown shorter than Fly's kill timeout.
"""

import re
import tomllib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DOCKERFILE = BACKEND_DIR / "Dockerfile"


def instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) for each Dockerfile instruction, continuation lines joined."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    found = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            found.append((word.upper(), rest.strip()))
    return found


def of(kind: str) -> list[str]:
    return [args for word, args in instructions() if word == kind]


def copy_sources(*, from_stage: bool) -> list[str]:
    """The source paths of every COPY, with or without `--from`."""
    sources = []
    for args in of("COPY"):
        if ("--from" in args) != from_stage:
            continue
        sources += [a for a in args.split() if not a.startswith("--")][:-1]
    return sources


def test_every_base_image_is_pinned_by_digest() -> None:
    froms = of("FROM")
    assert len(froms) >= 3  # uv, build, runtime
    for args in froms:
        assert re.match(r"^\S+@sha256:[0-9a-f]{64}( AS \w+)?$", args), args


def test_python_bases_are_3_13_slim_and_build_and_runtime_share_one() -> None:
    pythons = [a.split()[0] for a in of("FROM") if a.startswith("python:")]
    assert len(pythons) == 2
    assert len(set(pythons)) == 1
    assert re.match(r"^python:3\.13\.\d+-slim-\w+@sha256:", pythons[0])


def test_uv_image_tag_equals_required_version() -> None:
    pyproject = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    required = pyproject["tool"]["uv"]["required-version"].removeprefix("==")
    (uv,) = [a for a in of("FROM") if a.startswith("ghcr.io/astral-sh/uv:")]
    assert uv.startswith(f"ghcr.io/astral-sh/uv:{required}@sha256:")


def test_dependencies_install_from_the_lock_without_dev() -> None:
    (sync,) = [a for a in of("RUN") if "uv sync" in a]
    assert "--locked" in sync
    assert "--no-dev" in sync


def test_the_embedding_model_is_downloaded_at_build_from_settings() -> None:
    (download,) = [a for a in of("RUN") if "build_embedder" in a]
    assert "Settings(_env_file=None)" in download
    assert ".load()" in download
    assert not re.search(r"bge|BAAI", download)  # the name comes from config, not a second copy


def test_the_runtime_is_offline_for_huggingface() -> None:
    env = " ".join(of("ENV"))
    assert "HF_HUB_OFFLINE=1" in env
    assert "PYTHONUNBUFFERED=1" in env


def test_the_app_runs_as_a_non_root_user_set_after_the_last_copy() -> None:
    kinds = [word for word, _ in instructions()]
    (user_index,) = [i for i, word in enumerate(kinds) if word == "USER"]
    (user,) = of("USER")
    assert user not in ("root", "0") and not user.startswith("0")
    last_copy = max(i for i, word in enumerate(kinds) if word == "COPY")
    assert user_index > last_copy
    (useradd,) = [a for a in of("RUN") if "useradd" in a]
    assert "--uid 10001" in useradd


def test_port_and_command_match_fly_toml() -> None:
    fly = tomllib.loads((REPO_ROOT / "fly.toml").read_text(encoding="utf-8"))
    port = str(fly["http_service"]["internal_port"])
    assert of("EXPOSE") == [port]
    (cmd,) = of("CMD")
    assert cmd.startswith("[") and f'"--port", "{port}"' in cmd
    assert '"--host", "0.0.0.0"' in cmd


def test_the_command_drains_gracefully_and_logs_json() -> None:
    (cmd,) = of("CMD")
    assert '"--timeout-graceful-shutdown", "10"' in cmd
    assert '"--log-config", "log_config.json"' in cmd
    assert "--reload" not in cmd
    assert "--env-file" not in cmd  # production settings come from Fly secrets only


def test_nothing_secret_or_test_related_is_copied() -> None:
    sources = copy_sources(from_stage=False) + copy_sources(from_stage=True)
    assert sources
    for source in sources:
        assert source.strip("/") not in (".", "backend")
        assert not re.search(r"(^|/)\.env|(^|/)tests?(/|$)|(^|/)evals(/|$)", source), source
