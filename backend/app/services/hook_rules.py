"""Rules for Claude Code's PreToolUse hook (backend plan step 18). Pure code: no I/O.

These are guardrails, not the security boundary (credential isolation is, D-002). They catch the
obvious ways an agent goes around Blastproof: a database client, a credential file, the hook's own
settings, a force push, a recursive delete outside the workspace. Every denial carries a fixed
reason that never repeats the input.
"""

import base64
import binascii
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.services.masking import MASKED_PLACEHOLDER, mask_text

MAX_COMMAND_CHARS: Final = 100_000
SUMMARY_CHARS: Final = 500
MAX_NESTING: Final = 4
_WRAPPED_STARTS: Final = 8  # words after `sudo` that may start the wrapped command

REASONS: Final[dict[str, str]] = {
    "database_client": (
        "Blocked: direct database clients are not allowed. "
        "Use the blastproof MCP tools (query_database, list_tables, describe_table) instead."
    ),
    "credential_file": (
        "Blocked: this would read or change a credential file. "
        "Agents get no database credentials; use the blastproof MCP tools."
    ),
    "hook_settings": (
        "Blocked: the Claude Code settings that hold this guardrail "
        "cannot be read or changed by the agent."
    ),
    "hook_bypass": (
        "Blocked: starting Claude Code without its hooks, or with other settings, "
        "would skip Blastproof's guardrails."
    ),
    "terraform": "Blocked: terraform apply and destroy change real infrastructure; ask a person.",
    "git_force_push": "Blocked: force-pushing can destroy other people's commits. Push normally.",
    "rm_outside_workspace": (
        "Blocked: a recursive forced delete is only allowed on a literal path inside the "
        "workspace folder."
    ),
    "unparseable_command": "Blocked: the command could not be parsed safely. Write it more simply.",
    "too_long": "Blocked: the command is too long to check. Split it into smaller steps.",
}

SHELL_TOOLS: Final = frozenset({"Bash", "PowerShell", "Monitor"})
FILE_TOOLS: Final = frozenset({"Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit"})
DATABASE_CLIENTS: Final = frozenset({"psql", "pg_dump", "pg_dumpall", "pg_restore"})


@dataclass(frozen=True)
class HookVerdict:
    decision: Literal["allow", "deny"]
    rule: str | None
    reason: str


ALLOW: Final = HookVerdict("allow", None, "No rule matched.")


def _deny(rule: str) -> HookVerdict:
    return HookVerdict("deny", rule, REASONS[rule])


# --- paths ---------------------------------------------------------------------------------------

_DRIVE = re.compile(r"^[A-Za-z]:")
_GIT_BASH_DRIVE = re.compile(r"^/{1,2}([A-Za-z])(?=/|$)")


def _is_windows(text: str) -> bool:
    return bool(_DRIVE.match(text)) or "\\" in text


def _windows_form(text: str) -> str:
    text = text.replace("\\", "/")
    return _GIT_BASH_DRIVE.sub(lambda m: m.group(1) + ":", text)


def normalize(path: str, cwd: str) -> str:
    """An absolute, `/`-separated path with `..` resolved; lowercase on Windows.

    `~`, `$VAR` and `%VAR%` stay as they are: their value is unknown here.
    """
    windows = _is_windows(path) or _is_windows(cwd)
    text = path.strip()
    base = cwd
    if windows:
        text, base = _windows_form(text), _windows_form(base)
    if text.startswith(("~", "$", "%")):
        return posixpath.normpath(text)
    if not (text.startswith("/") or _DRIVE.match(text)):
        text = base.rstrip("/") + "/" + text
    drive_match = _DRIVE.match(text)
    drive = drive_match.group(0) if drive_match else ""
    rest = re.sub(r"/{2,}", "/", text[len(drive) :]) or "/"
    result = drive + posixpath.normpath(rest)
    return result.lower() if windows else result


def _inside(path: str, project_dir: str) -> bool:
    return path.startswith(project_dir.rstrip("/") + "/")


# --- names of files that must not be touched ---------------------------------------------------

_CREDENTIAL_NAMES = re.compile(
    r"(?<![\w.-])\.env(?:\.[\w.*?-]*|[*?])?(?![\w-])"
    r"|(?<![\w.-])(?:\.pgpass|pgpass\.conf|\.netrc|\.git-credentials|\.claude\.json)(?![\w-])"
    r"|(?<![\w-])id_(?:rsa|ed25519|ecdsa)"
    r"|\.(?:pem|key|p12|pfx)(?![\w-])"
    r"|(?<![\w.-])\.ssh(?![\w-])"
    r"|(?<![\w.-])\.aws/credentials"
    r"|(?<![\w.-])\.config/neonctl"
)
_HOOK_SETTINGS = re.compile(r"(?<![\w-])\.claude/settings|(?<![\w-])settings\.local\.json")


def _flat(text: str) -> str:
    return text.replace("\\", "/").lower()


def names_credential(text: str) -> bool:
    return _CREDENTIAL_NAMES.search(_flat(text)) is not None


def names_hook_settings(text: str) -> bool:
    return _HOOK_SETTINGS.search(_flat(text)) is not None


# --- shell commands ----------------------------------------------------------------------------

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATOR = re.compile(r"^[();&|]+$")
_SKIP_WORDS = frozenset(
    {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "&"}
)
_WRAPPERS = frozenset({"sudo", "env", "command", "exec", "nohup", "time", "xargs", "nice", "doas"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_POWERSHELLS = frozenset({"pwsh", "powershell"})
_EVALS = frozenset({"eval", "invoke-expression", "iex"})
_RM_FAMILY = frozenset({"rm", "remove-item", "ri", "del", "erase", "rd", "rmdir"})
_CMD_RM = frozenset({"del", "erase", "rd", "rmdir"})
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


class Unparseable(Exception):
    pass


def program_name(token: str) -> str:
    name = re.split(r"[/\\]", token)[-1].lower()
    return re.sub(r"\.(?:exe|cmd|bat|com)$", "", name)


def split_commands(text: str, shell: Literal["bash", "powershell"]) -> list[list[str]]:
    """The simple commands in `text`, each as a list of words. Raises Unparseable."""
    prepared = text.replace("\r\n", "\n").replace("\n", ";").replace("`", ";")
    prepared = prepared.replace("{", " { ").replace("}", " } ")
    lexer = shlex.shlex(prepared, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if shell == "powershell":
        lexer.escape = ""
    try:
        tokens = list(lexer)
    except ValueError:
        raise Unparseable from None
    commands: list[list[str]] = [[]]
    for token in tokens:
        if _SEPARATOR.match(token):
            commands.append([])
        else:
            commands[-1].append(token)
    return [c for c in commands if c]


def _strip_leading(command: list[str]) -> list[str]:
    i = 0
    while i < len(command) and (command[i] in _SKIP_WORDS or _ASSIGNMENT.match(command[i])):
        i += 1
    return command[i:]


def _program_views(command: list[str]) -> list[list[str]]:
    """The command itself plus, after a wrapper (`sudo -u x psql`), every later word as a start."""
    command = _strip_leading(command)
    if not command:
        return []
    views = [command]
    if program_name(command[0]) in _WRAPPERS:
        starts = [i for i in range(1, len(command)) if not command[i].startswith("-")]
        for i in starts[:_WRAPPED_STARTS]:
            views.extend(_program_views(command[i:]))
    return views


def _nested_scripts(command: list[str]) -> list[tuple[str, Literal["bash", "powershell"]]]:
    program, rest = program_name(command[0]), command[1:]
    if program in _POSIX_SHELLS:
        for i, word in enumerate(rest[:-1]):
            if re.fullmatch(r"-[A-Za-z]*c", word):
                return [(rest[i + 1], "bash")]
    elif program in _POWERSHELLS:
        for i, word in enumerate(rest):
            low = word.lower()
            if low.startswith("-c") and "-command".startswith(low) and len(low) >= 2:
                return [(" ".join(rest[i + 1 :]), "powershell")]
            if low.startswith("-e") and "-encodedcommand".startswith(low) and i + 1 < len(rest):
                try:
                    return [(base64.b64decode(rest[i + 1]).decode("utf-16-le"), "powershell")]
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    raise Unparseable from None
    elif program == "cmd":
        for i, word in enumerate(rest):
            if word.lower() in ("/c", "/k"):
                return [(" ".join(rest[i + 1 :]), "powershell")]
    elif program in _EVALS and rest:
        return [(" ".join(rest), "powershell" if program != "eval" else "bash")]
    return []


def _short_combo(low: str, letter: str) -> bool:
    """`-rf`, `-fr`, `-Rf`: a few single-letter flags in one word (not PowerShell's `-force`)."""
    return re.fullmatch(r"-[a-z]{1,4}", low) is not None and letter in low


def _is_recursive(flag: str) -> bool:
    low = flag.lower()
    if low.startswith("--"):
        return low == "--recursive"
    return (len(low) >= 2 and "-recurse".startswith(low)) or _short_combo(low, "r")


def _is_force(flag: str) -> bool:
    low = flag.lower()
    if low.startswith("--"):
        return low == "--force"
    return (len(low) >= 3 and "-force".startswith(low)) or _short_combo(low, "f")


def _rm_outside(command: list[str], cwd: str, project_dir: str) -> bool:
    """True for a recursive, forced delete whose target is not strictly inside the workspace."""
    program, rest = program_name(command[0]), command[1:]
    flags: list[str] = []
    targets: list[str] = []
    after_double_dash = False
    for word in rest:
        if after_double_dash:
            targets.append(word)
        elif word == "--":
            after_double_dash = True
        elif word.startswith("-"):
            flags.append(word)
        elif program in _CMD_RM and word.lower() in ("/s", "/q"):
            flags.append("-r" if word.lower() == "/s" else "-f")
        else:
            targets.append(word)
    if not (any(_is_recursive(f) for f in flags) and any(_is_force(f) for f in flags)):
        return False
    for target in targets:
        if target.startswith(("~", "$", "%")):
            return True
        if not _inside(normalize(target, cwd), project_dir):
            return True
    return False


def _git_force_push(rest: list[str]) -> bool:
    i = 0
    while i < len(rest):
        word = rest[i]
        if word in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2
        elif word.startswith("-"):
            i += 1
        else:
            break
    if i >= len(rest) or rest[i] != "push":
        return False
    for word in rest[i + 1 :]:
        if word.startswith("--force") or re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", word):
            return True
        if len(word) > 1 and word.startswith("+"):
            return True
    return False


def _terraform_change(rest: list[str]) -> bool:
    for word in rest:
        if not word.startswith("-"):
            return word.lower() in ("apply", "destroy")
    return False


def _check_command(command: list[str], cwd: str, project_dir: str) -> str | None:
    program, rest = program_name(command[0]), command[1:]
    if program in DATABASE_CLIENTS:
        return "database_client"
    if program == "claude" and any(
        w in ("--bare", "--settings", "--setting-sources")
        or w.startswith(("--settings=", "--setting-sources="))
        for w in rest
    ):
        return "hook_bypass"
    if program == "terraform" and _terraform_change(rest):
        return "terraform"
    if program == "git" and _git_force_push(rest):
        return "git_force_push"
    if program in _RM_FAMILY and _rm_outside(command, cwd, project_dir):
        return "rm_outside_workspace"
    return None


def _check_script(
    text: str, shell: Literal["bash", "powershell"], cwd: str, project_dir: str, depth: int
) -> str | None:
    if depth > MAX_NESTING:
        return "unparseable_command"
    try:
        commands = split_commands(text, shell)
        for command in commands:
            for view in _program_views(command):
                rule = _check_command(view, cwd, project_dir)
                if rule:
                    return rule
                for script, nested_shell in _nested_scripts(view):
                    rule = _check_script(script, nested_shell, cwd, project_dir, depth + 1)
                    if rule:
                        return rule
        for match in _SUBSTITUTION.finditer(text):
            inner = match.group(1) or match.group(2) or ""
            rule = _check_script(inner, shell, cwd, project_dir, depth + 1)
            if rule:
                return rule
    except Unparseable:
        return "unparseable_command"
    return None


def _check_shell(tool_name: str, command: str, cwd: str, project_dir: str) -> str | None:
    if len(command) > MAX_COMMAND_CHARS:
        return "too_long"
    if "disableallhooks" in command.lower():
        return "hook_bypass"
    if names_hook_settings(command):
        return "hook_settings"
    if names_credential(command):
        return "credential_file"
    shell: Literal["bash", "powershell"] = "powershell" if tool_name == "PowerShell" else "bash"
    return _check_script(command, shell, cwd, project_dir, 0)


# --- the entry point ---------------------------------------------------------------------------


def _file_targets(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    keys = {
        "Read": ("file_path",),
        "Edit": ("file_path",),
        "Write": ("file_path",),
        "NotebookEdit": ("notebook_path", "file_path"),
        "Grep": ("path", "glob"),
        "Glob": ("pattern", "path"),
    }[tool_name]
    return [v for k in keys if isinstance(v := tool_input.get(k), str)]


def evaluate(
    tool_name: str, tool_input: dict[str, Any], cwd: str, project_dir: str | None
) -> HookVerdict:
    workspace = normalize(project_dir or cwd, cwd) if (project_dir or cwd) else "/"
    if tool_name in SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str):
            return ALLOW  # Monitor's WebSocket form carries no command
        rule = _check_shell(tool_name, command, cwd, workspace)
    elif tool_name in FILE_TOOLS:
        rule = None
        for target in _file_targets(tool_name, tool_input):
            if names_hook_settings(target):
                rule = "hook_settings"
            elif names_credential(target):
                rule = "credential_file"
            if rule:
                break
    else:
        return ALLOW
    return _deny(rule) if rule else ALLOW


# --- the masked summary stored in hook_events ----------------------------------------------------

_SCRUBS: Final = (
    (re.compile(r"(\w+://[^:/\s@]+:)[^@\s/]+(@)"), rf"\1{MASKED_PLACEHOLDER}\2"),
    (re.compile(r"bp_[A-Za-z0-9_-]{20,}"), MASKED_PLACEHOLDER),
    (
        re.compile(r"(--password(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|\S+)", re.I),
        rf"\1{MASKED_PLACEHOLDER}",
    ),
    (
        re.compile(r"(\w*(?:PASSWORD|TOKEN|SECRET|KEY)=)(?:\"[^\"]*\"|'[^']*'|\S+)", re.I),
        rf"\1{MASKED_PLACEHOLDER}",
    ),
)


def summarize(tool_name: str, tool_input: dict[str, Any]) -> str:
    """What the call was, with secrets, emails and phone numbers masked, at most 500 characters."""
    if tool_name in SHELL_TOOLS:
        command = tool_input.get("command")
        detail = command if isinstance(command, str) else "(no command)"
    elif tool_name in FILE_TOOLS:
        detail = " ".join(_file_targets(tool_name, tool_input)) or "(no path)"
    else:
        detail = "(not summarised)"
    text = f"{tool_name}: {detail}"
    for pattern, replacement in _SCRUBS:
        text = pattern.sub(replacement, text)
    return mask_text(text)[:SUMMARY_CHARS]
