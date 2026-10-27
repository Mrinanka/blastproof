"""Step 18: claude_hook/pre_tool_use.py against a fake loopback server (no Docker)."""

import ast
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.services.hook_rules import evaluate
from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "claude_hook" / "pre_tool_use.py"
KEY = "bp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U_v"
CWD = str(REPO_ROOT.parent / "blastproof-demo")


@dataclass
class Seen:
    times: list[float] = field(default_factory=list)
    bodies: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    raw: list[bytes] = field(default_factory=list)


Responder = Callable[[], tuple[int, bytes]]


class FakeBlastproof:
    def __init__(self) -> None:
        self.seen = Seen()
        self.respond: Responder = lambda: (200, b'{"decision":"allow","reason":null,"rule":null}')
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.seen.times.append(time.monotonic())
                outer.seen.raw.append(body)
                outer.seen.bodies.append(json.loads(body))
                outer.seen.headers.append(dict(self.headers))
                time.sleep(outer.delay)
                status, payload = outer.respond()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # the script gave up waiting

            def log_message(self, *_args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake() -> Iterator[FakeBlastproof]:
    server = FakeBlastproof()
    yield server
    server.stop()


@pytest.fixture
def down() -> Iterator[str]:
    """A backend that answers 503 at once: the quick stand-in for 'down' in the bulk tests."""
    server = FakeBlastproof()
    server.respond = lambda: (503, b"")
    yield server.url
    server.stop()


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


@dataclass
class Run:
    code: int
    out: str
    err: str
    seconds: float


def run_hook(
    event: Any,
    url: str,
    *,
    key: str | None = KEY,
    extra: list[str] | None = None,
    raw_stdin: str | None = None,
) -> Run:
    env = {k: v for k, v in os.environ.items() if k != "BLASTPROOF_AGENT_KEY"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    if key is not None:
        env["BLASTPROOF_AGENT_KEY"] = key
    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--url",
            url,
            *(["--retry-pause", "0"] if extra is None else extra),
        ],
        input=raw_stdin if raw_stdin is not None else json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return Run(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started)


def event(tool: str, **tool_input: Any) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": CWD,
    }


ALLOW = (200, b'{"decision":"allow","reason":null,"rule":null}')


def deny(reason: str) -> tuple[int, bytes]:
    return 200, json.dumps({"decision": "deny", "reason": reason, "rule": "x"}).encode()


# --- the backend answers -------------------------------------------------------------------------


def test_allow_prints_nothing_and_exits_0(fake: FakeBlastproof) -> None:
    result = run_hook(event("Bash", command="ls"), fake.url)
    assert (result.code, result.out, result.err) == (0, "", "")


def test_deny_exits_2_with_the_reason_on_stderr(fake: FakeBlastproof) -> None:
    fake.respond = lambda: deny("Blocked: no psql.")
    result = run_hook(event("Bash", command="psql"), fake.url)
    assert result.code == 2
    assert result.err.strip() == "Blocked: no psql."
    assert result.out == ""
    assert len(fake.seen.bodies) == 1  # no retry on an answer


@pytest.mark.parametrize("tool", ["Bash", "Read", "Edit", "Grep"])
def test_401_denies_every_tool_without_retrying(fake: FakeBlastproof, tool: str) -> None:
    fake.respond = lambda: (401, b'{"error":{"code":"invalid_agent_key","message":"x"}}')
    result = run_hook(event(tool, command="ls", file_path="README.md"), fake.url)
    assert result.code == 2
    assert "key was rejected" in result.err
    assert len(fake.seen.bodies) == 1


@pytest.mark.parametrize("key", [None, ""])
def test_a_missing_or_empty_key_denies_without_calling(
    fake: FakeBlastproof, key: str | None
) -> None:
    result = run_hook(event("Read", file_path="README.md"), fake.url, key=key)
    assert result.code == 2
    assert "key was rejected" in result.err
    assert fake.seen.bodies == []


# --- Blastproof is unreachable or confused ---------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        (500, b"oops"),
        (503, b""),
        (422, b'{"error":{}}'),
        (404, b"no"),
        (200, b"not json"),
        (200, b'{"decision":"maybe"}'),
        (200, b"[]"),
    ],
)
def test_other_answers_count_as_unreachable_and_retry_once(
    fake: FakeBlastproof, response: tuple[int, bytes]
) -> None:
    fake.respond = lambda: response
    bash = run_hook(event("Bash", command="ls"), fake.url)
    assert bash.code == 2 and "could not be reached" in bash.err
    assert len(fake.seen.bodies) == 2
    read = run_hook(event("Read", file_path="README.md"), fake.url)
    assert read.code == 0 and read.err == ""


def test_two_attempts_about_a_second_apart(fake: FakeBlastproof) -> None:
    fake.respond = lambda: (500, b"")
    run_hook(event("Bash", command="ls"), fake.url, extra=[])
    first, second = fake.seen.times
    assert 0.9 <= second - first < 3


def test_a_retry_that_works_is_used(fake: FakeBlastproof) -> None:
    answers = iter([(500, b""), ALLOW])
    fake.respond = lambda: next(answers)
    result = run_hook(event("Bash", command="ls"), fake.url)
    assert (result.code, result.err) == (0, "")


def test_a_slow_server_is_unreachable_within_the_hook_timeout(fake: FakeBlastproof) -> None:
    fake.delay = 4
    result = run_hook(event("Bash", command="ls"), fake.url, extra=["--timeout", "0.5"])
    assert result.code == 2
    assert result.seconds < 10  # the workspace's hook timeout


# Named files: the backend's rules deny these too.
OFFLINE_NAMED_CALLS = [
    ("Read", {"file_path": ".env"}),
    ("Read", {"file_path": str(REPO_ROOT / "backend" / ".env")}),
    ("Read", {"file_path": ".claude/settings.local.json"}),
    ("Read", {"file_path": "~/.pgpass"}),
    ("Read", {"file_path": "~/.ssh/id_rsa"}),
    ("Read", {"file_path": "server.pem"}),
    ("Grep", {"pattern": "x", "path": "..", "glob": ".env*"}),
    ("Glob", {"pattern": "**/.env"}),
]
# Anywhere in this repository: offline only (D-117 widened BE-103's "backend directory").
OFFLINE_REPO_CALLS = [
    ("Read", {"file_path": str(REPO_ROOT / "docker-compose.yml")}),
    ("Grep", {"pattern": "x", "path": str(REPO_ROOT)}),
    ("Glob", {"pattern": "**/*.py", "path": str(REPO_ROOT / "backend")}),
]
OFFLINE_DENIED_CALLS = OFFLINE_NAMED_CALLS + OFFLINE_REPO_CALLS


@pytest.mark.parametrize(("tool", "tool_input"), OFFLINE_DENIED_CALLS)
def test_down_denies_reads_of_sensitive_paths(
    tool: str, tool_input: dict[str, Any], down: str
) -> None:
    result = run_hook(event(tool, **tool_input), down)
    assert result.code == 2, (tool, tool_input)
    assert "not readable" in result.err


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "README.md"}),
        ("Read", {"file_path": str(Path(CWD) / "src" / "app.py")}),
        ("Grep", {"pattern": "x", "path": "src"}),
        ("Glob", {"pattern": "**/*.md"}),
    ],
)
def test_down_allows_reads_of_ordinary_files(
    tool: str, tool_input: dict[str, Any], down: str
) -> None:
    result = run_hook(event(tool, **tool_input), down)
    assert (result.code, result.out, result.err) == (0, "", "")


@pytest.mark.parametrize(
    "tool", ["Bash", "PowerShell", "Monitor", "Edit", "Write", "NotebookEdit", "SomethingNew"]
)
def test_down_denies_everything_else(tool: str, down: str) -> None:
    result = run_hook(event(tool, command="ls", file_path="README.md"), down)
    assert result.code == 2
    assert "could not be reached" in result.err


def test_a_really_closed_port_behaves_the_same() -> None:
    url = closed_port_url()
    assert run_hook(event("Bash", command="ls"), url).code == 2
    assert run_hook(event("Edit", file_path="a.py"), url).code == 2
    assert run_hook(event("Read", file_path="README.md"), url).code == 0
    assert run_hook(event("Read", file_path=".env"), url).code == 2


def test_every_offline_denied_name_is_also_denied_by_the_backend_rules() -> None:
    """The two lists must not drift: a named file denied offline is denied online too."""
    for tool, tool_input in OFFLINE_NAMED_CALLS:
        assert evaluate(tool, tool_input, CWD, CWD).decision == "deny", (tool, tool_input)


# --- input and payload ---------------------------------------------------------------------------


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '"x"', "{}", '{"tool_name": 5}'])
def test_bad_stdin_exits_2(fake: FakeBlastproof, stdin: str) -> None:
    result = run_hook(None, fake.url, raw_stdin=stdin)
    assert result.code == 2
    assert result.out == ""
    assert fake.seen.bodies == []


def test_the_payload_has_no_file_bodies_and_the_key_only_in_the_header(
    fake: FakeBlastproof,
) -> None:
    call = event(
        "Edit",
        file_path="src/app.py",
        old_string="PASSWORD=old",
        new_string="PASSWORD=new",
        content="C" * 1000,
        new_source="print(1)",
    )
    result = run_hook(call, fake.url)
    assert result.code == 0
    [body] = fake.seen.bodies
    assert body["tool_name"] == "Edit"
    assert body["tool_input"] == {"file_path": "src/app.py"}
    assert body["cwd"] == CWD
    assert body["project_dir"] == CWD
    [raw] = fake.seen.raw
    assert KEY.encode() not in raw
    assert fake.seen.headers[0]["Authorization"] == f"Bearer {KEY}"


def test_project_dir_comes_from_claude_project_dir(fake: FakeBlastproof) -> None:
    env_key = {"CLAUDE_PROJECT_DIR": "/somewhere/else"}
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--url", fake.url],
        input=json.dumps(event("Bash", command="ls")),
        capture_output=True,
        text=True,
        env={**os.environ, "BLASTPROOF_AGENT_KEY": KEY, **env_key},
        timeout=60,
    )
    assert proc.returncode == 0
    assert fake.seen.bodies[0]["project_dir"] == "/somewhere/else"


def test_the_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert modules
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


def test_the_script_never_prints_the_key(fake: FakeBlastproof) -> None:
    fake.respond = lambda: (500, b"")
    result = run_hook(event("Bash", command="ls"), fake.url)
    assert KEY not in result.out + result.err
