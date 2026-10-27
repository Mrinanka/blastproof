"""Claude Code PreToolUse hook: asks Blastproof before every shell, edit and read tool call.

Standard library only. Registered by `workspace.py create` as:

    python pre_tool_use.py --url http://localhost:8000 [--timeout 3]

The agent key comes only from the BLASTPROOF_AGENT_KEY environment variable. Exit codes: 0 leaves
Claude Code's normal permission flow alone (nothing is printed); 2 blocks the call and the reason
on stderr reaches the agent.

If Blastproof cannot be reached, shell and edit tools (and any tool we do not know) are blocked.
Read, Grep and Glob fall back to a local deny-list of credential files, the hook's own settings
and this repository, so a restart does not stop the agent reading ordinary files.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

KEY_VAR = "BLASTPROOF_AGENT_KEY"
ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 3.0
DROPPED_FIELDS = ("content", "old_string", "new_string", "new_source")
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
REPO = Path(__file__).resolve().parent.parent

BAD_INPUT = "Blocked: the hook input could not be read."
UNREACHABLE = "Blocked: Blastproof could not be reached to check this call, so it was not run."
KEY_REJECTED = "Blocked: the agent key was rejected, so Blastproof cannot check this call."
OFFLINE_DENIED = "Blocked: this path is not readable while Blastproof is unreachable."

# Names a read of which is refused offline (the same families as the backend's credential_file and
# hook_settings rules, plus the whole repository: docker-compose.yml holds local passwords).
_OFFLINE_NAMES = re.compile(
    r"(?<![\w.-])\.env(?:\.[\w.*?-]*|[*?])?(?![\w-])"
    r"|(?<![\w.-])(?:\.pgpass|pgpass\.conf|\.netrc|\.git-credentials|\.claude\.json)(?![\w-])"
    r"|(?<![\w-])id_(?:rsa|ed25519|ecdsa)"
    r"|\.(?:pem|key|p12|pfx)(?![\w-])"
    r"|(?<![\w.-])\.ssh(?![\w-])"
    r"|(?<![\w.-])\.aws/credentials"
    r"|(?<![\w.-])\.config/neonctl"
    r"|(?<![\w-])\.claude/settings"
    r"|(?<![\w-])settings\.local\.json"
)


_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:)?[\\/]")  # a drive and/or a leading slash or backslash


class Unreachable(Exception):
    pass


class KeyRejected(Exception):
    pass


def _flat(path: str) -> str:
    return path.replace("\\", "/").lower()


def _in_repo(path: str) -> bool:
    flat = _flat(path)
    if flat.startswith("~") or not re.match(r"^(?:[a-z]:)?/", flat):
        return False
    repo = _flat(str(REPO)).rstrip("/")
    return flat == repo or flat.startswith(repo + "/")


def offline_denied(tool_name: str, tool_input: dict[str, Any], cwd: str) -> bool:
    """True when a read-only tool names something that must stay unreadable without Blastproof."""
    keys = {"Read": ("file_path",), "Grep": ("path", "glob"), "Glob": ("pattern", "path")}[
        tool_name
    ]
    for key in keys:
        value = tool_input.get(key)
        if not isinstance(value, str):
            continue
        if _OFFLINE_NAMES.search(_flat(value)):
            return True
        absolute = value if _ABSOLUTE.match(value) else f"{cwd}/{value}"
        if _in_repo(os.path.normpath(absolute)):
            return True
    return False


def build_payload(event: dict[str, Any]) -> dict[str, Any]:
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    return {
        "tool_name": event.get("tool_name"),
        "tool_input": {k: v for k, v in tool_input.items() if k not in DROPPED_FIELDS},
        "cwd": cwd,
        "project_dir": os.environ.get("CLAUDE_PROJECT_DIR") or cwd,
    }


def ask_blastproof(
    url: str, key: str, payload: dict[str, Any], timeout: float, pause: float = RETRY_PAUSE_SECONDS
) -> dict[str, Any]:
    """The backend's verdict. Raises KeyRejected on 401 and Unreachable on anything else wrong."""
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/hook/pre-tool-use",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(pause)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise KeyRejected from None
            last = exc
            continue
        except (OSError, ValueError) as exc:  # connection, timeout, bad JSON
            last = exc
            continue
        if isinstance(body, dict) and body.get("decision") in ("allow", "deny"):
            return body
        last = ValueError("unknown decision")
    raise Unreachable from last


def decide(
    event: dict[str, Any], url: str, timeout: float, pause: float = RETRY_PAUSE_SECONDS
) -> tuple[int, str]:
    """(exit code, stderr text) for one hook event."""
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return 2, BAD_INPUT
    payload = build_payload(event)
    key = os.environ.get(KEY_VAR, "")
    if not key:
        return 2, KEY_REJECTED
    try:
        verdict = ask_blastproof(url, key, payload, timeout, pause)
    except KeyRejected:
        return 2, KEY_REJECTED
    except Exception:  # noqa: BLE001 - any failure here is "unreachable", and that fails closed
        if tool_name in READ_TOOLS and not offline_denied(
            tool_name, payload["tool_input"], payload["cwd"]
        ):
            return 0, ""
        return 2, OFFLINE_DENIED if tool_name in READ_TOOLS else UNREACHABLE
    if verdict["decision"] == "deny":
        reason = verdict.get("reason")
        return 2, reason if isinstance(reason, str) and reason else "Blocked by Blastproof."
    return 0, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blastproof PreToolUse hook")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--retry-pause", type=float, default=RETRY_PAUSE_SECONDS)
    args = parser.parse_args(argv)
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("not an object")
    except ValueError:
        print(BAD_INPUT, file=sys.stderr)
        return 2
    code, message = decide(event, args.url, args.timeout, args.retry_pause)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
