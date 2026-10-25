"""Demo workspace for Claude Code: no company database credential, guardrails on the repo.

Standard library only. Run from anywhere:

    uv run --project backend python claude_hook/workspace.py create <workspace> [--force] \
        [--key-stdin]
    uv run --project backend python claude_hook/workspace.py check <workspace>
    uv run --project backend python claude_hook/workspace.py verify <workspace>

`create` writes four files. `check` scans the folder and the current environment for anything that
looks like a database credential and prints only file paths and variable names, never values.
`verify` drives `claude -p` to confirm the guardrails; it needs a Claude login and a trusted folder.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = REPO.parent / "blastproof-demo"
DEFAULT_BASE_URL = "http://localhost:8000"
KEY_VAR = "BLASTPROOF_AGENT_KEY"
KEY_PATTERN = re.compile(r"bp_[A-Za-z0-9_-]{43}")

# What must not appear in the workspace or its environment (DW-B07).
SCAN_PATTERNS = (
    "postgres",
    "neon.tech",
    "PGPASSWORD",
    "DATABASE_URL",
    "NEON_API_KEY",
)
_SCAN_TEXT = re.compile("|".join(re.escape(p) for p in SCAN_PATTERNS), re.IGNORECASE)
_SCAN_BYTES = re.compile("|".join(re.escape(p) for p in SCAN_PATTERNS).encode(), re.IGNORECASE)

CLAUDE_MD = """# Demo workspace

Company data is reachable only through the `blastproof` MCP tools (`query_database`,
`list_tables`, `describe_table`, `get_action_status`). There is no direct connection to it from
this folder and no credential for one: do not look for one.
"""


@dataclass(frozen=True)
class Finding:
    kind: Literal["file", "env", "symlink"]
    where: str


def matches(text: str) -> bool:
    return _SCAN_TEXT.search(text) is not None


def claude_path(path: str | Path) -> str:
    """`D:\\My Workplace\\Blastproof` becomes `//d/My Workplace/Blastproof` (spike D-115)."""
    text = str(path).replace("\\", "/")
    drive = re.match(r"^([A-Za-z]):/", text)
    if drive:
        return f"//{drive.group(1).lower()}/{text[3:]}"
    return "//" + text.lstrip("/")  # Claude Code writes an absolute path with two slashes


def deny_rules(repo: Path) -> list[str]:
    return [
        f"Read({claude_path(repo)}/**)",
        "Read(//**/.env)",
        "Read(//**/.env.*)",
        "Read(//**/.pgpass)",
        "Read(//**/pgpass.conf)",
        "Read(~/.config/neonctl/**)",
        "Read(~/.ssh/**)",
        "Read(~/.claude.json)",
        "Edit(.claude/**)",
        "Edit(.mcp.json)",
        "Edit(//**/.env)",
        "Edit(//**/.env.*)",
        # `docker exec` into the local databases needs no password (D-121).
        "Bash(docker)",
        "Bash(docker *)",
        "PowerShell(docker)",
        "PowerShell(docker *)",
    ]


def build_files(repo: Path, base_url: str, key: str) -> dict[str, str]:
    settings = {
        "permissions": {
            "deny": deny_rules(repo),
            "blockReadsOutsideWorkingDirectories": True,
        },
        # No claude.ai connector (for example a Neon or Postgres one) reaches the agent (D-121).
        "disableClaudeAiConnectors": True,
    }
    local = {"env": {KEY_VAR: key}, "enabledMcpjsonServers": ["blastproof"]}
    mcp = {
        "mcpServers": {
            "blastproof": {
                "type": "http",
                "url": base_url.rstrip("/") + "/mcp",
                "headers": {"Authorization": f"Bearer ${{{KEY_VAR}}}"},
            }
        }
    }
    return {
        ".claude/settings.json": json.dumps(settings, indent=2) + "\n",
        ".claude/settings.local.json": json.dumps(local, indent=2) + "\n",
        ".mcp.json": json.dumps(mcp, indent=2) + "\n",
        "CLAUDE.md": CLAUDE_MD,
    }


class CreateError(Exception):
    pass


def create(ws: Path, repo: Path, base_url: str, key: str, force: bool) -> None:
    ws, repo = ws.resolve(), repo.resolve()
    if ws == repo or repo in ws.parents:
        raise CreateError("the workspace must be outside the repo")
    if ws in repo.parents:
        raise CreateError("the workspace would contain the repo; pick a sibling folder")
    if ws.exists() and any(ws.iterdir()) and not force:
        raise CreateError("the workspace folder is not empty; use --force to rewrite our files")
    for name, text in build_files(repo, base_url, key).items():
        target = ws / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def scan(ws: Path, environ: Mapping[str, str]) -> list[Finding]:
    found: list[Finding] = []
    for name, value in environ.items():
        if matches(name) or matches(value):
            found.append(Finding("env", name))
    for root, dirs, files in os.walk(ws, followlinks=False):
        base = Path(root)
        for entry in [*dirs, *files]:
            path = base / entry
            rel = path.relative_to(ws).as_posix()
            if path.is_symlink():
                found.append(Finding("symlink", rel))
            elif matches(entry) or (path.is_file() and _file_matches(path)):
                found.append(Finding("file", rel))
    return found


def _file_matches(path: Path) -> bool:
    try:
        return _SCAN_BYTES.search(path.read_bytes()) is not None
    except OSError:
        return True  # fail closed: a file we cannot read is a finding


# --- verify ------------------------------------------------------------------------------------


def _run_claude(ws: Path, prompt: str, model: str = "haiku") -> list[dict]:
    proc = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--permission-mode",
            "bypassPermissions",
        ],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _tool_results(events: list[dict]) -> list[dict]:
    results = []
    for event in events:
        content = event.get("message", {}).get("content") if event.get("type") == "user" else None
        if isinstance(content, list):
            results.extend(b for b in content if b.get("type") == "tool_result")
    return results


def _check_env(ws: Path) -> bool:
    events = _run_claude(
        ws,
        "Run `env` with the Bash tool and `Get-ChildItem env:` with the PowerShell tool. "
        "Do not summarize the output.",
    )
    results = _tool_results(events)
    return bool(results) and not any(matches(json.dumps(r.get("content"))) for r in results)


def _check_denied(ws: Path, path: Path, canary: str | None) -> bool:
    events = _run_claude(
        ws,
        f"This is an automated permission test. Call the Read tool once on this exact path and "
        f"report only whether the tool call was allowed or denied: {path}",
        model="sonnet",  # Haiku declines to attempt the call, which proves nothing
    )
    results = _tool_results(events)
    everything = json.dumps(events)
    leaked = canary is not None and canary in everything
    return bool(results) and all(r.get("is_error") for r in results) and not leaked


def verify(ws: Path, repo: Path) -> bool:
    ok = True
    env_ok = _check_env(ws)
    print(f"{'PASS' if env_ok else 'FAIL'}  environment shows no database credential")
    ok &= env_ok

    canary = "canary-" + secrets.token_hex(8)  # the content; the file name must not repeat it
    canary_file = repo / "backend" / f"bp-verify-{secrets.token_hex(4)}.txt"
    canary_file.write_text(canary, encoding="utf-8")
    try:
        canary_ok = _check_denied(ws, canary_file, canary)
    finally:
        canary_file.unlink(missing_ok=True)
    print(f"{'PASS' if canary_ok else 'FAIL'}  Read of a canary file in backend/ is denied")
    ok &= canary_ok

    if canary_ok:
        env_file = repo / "backend" / ".env"
        real_ok = _check_denied(ws, env_file, None)
        print(f"{'PASS' if real_ok else 'FAIL'}  Read of the backend's .env is denied")
        ok &= real_ok
    else:
        print("SKIP  Read of the backend's .env (the canary was not denied)")
    return ok


# --- command line ------------------------------------------------------------------------------


def _read_key() -> str:
    raw = sys.stdin.read()
    line = raw[:-2] if raw.endswith("\r\n") else raw[:-1] if raw.endswith("\n") else raw
    if not KEY_PATTERN.fullmatch(line):
        raise CreateError(
            "the key on stdin must be one line: bp_ followed by 43 URL-safe characters"
        )
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("workspace", type=Path, nargs="?", default=DEFAULT_WORKSPACE)
    p_create.add_argument("--force", action="store_true")
    p_create.add_argument("--key-stdin", action="store_true")
    p_create.add_argument("--base-url", default=os.environ.get("PUBLIC_BASE_URL", DEFAULT_BASE_URL))
    for name in ("check", "verify"):
        p = sub.add_parser(name)
        p.add_argument("workspace", type=Path, nargs="?", default=DEFAULT_WORKSPACE)
    args = parser.parse_args(argv)

    if args.command == "create":
        try:
            key = _read_key() if args.key_stdin else ""
            create(args.workspace, REPO, args.base_url, key, args.force)
        except CreateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"workspace written to {args.workspace.resolve()}")
        return 0

    if not args.workspace.is_dir():
        print(f"error: {args.workspace} is not a folder", file=sys.stderr)
        return 2
    if args.command == "check":
        findings = scan(args.workspace.resolve(), os.environ)
        for f in findings:
            print(f"{f.kind}: {f.where}")
        print("clean" if not findings else f"{len(findings)} finding(s)")
        return 1 if findings else 0
    return 0 if verify(args.workspace.resolve(), REPO) else 1


if __name__ == "__main__":
    sys.exit(main())
