"""Step 18: the hook rules (pure code, no Docker). Every denial has a fixed reason."""

import base64
import time
from pathlib import Path
from typing import Any

import pytest

from app.services import hook_rules
from app.services.hook_rules import REASONS, evaluate, normalize, split_commands, summarize
from evals.schema import HookScenario, load_scenario_file

WIN_CWD = r"D:\My Workplace\blastproof-demo"
POSIX_CWD = "/home/me/demo"


def verdict(tool: str, tool_input: dict[str, Any], cwd: str = WIN_CWD) -> hook_rules.HookVerdict:
    return evaluate(tool, tool_input, cwd, cwd)


def bash(command: str, cwd: str = WIN_CWD) -> hook_rules.HookVerdict:
    return verdict("Bash", {"command": command}, cwd)


def rule_of(tool: str, tool_input: dict[str, Any], cwd: str = WIN_CWD) -> str | None:
    return verdict(tool, tool_input, cwd).rule


# --- shell commands ---------------------------------------------------------------------------

DENIED_COMMANDS = [
    # database_client
    ("psql -h db -c 'select 1'", "database_client"),
    ("PGPASSWORD=x psql -c 'select 1'", "database_client"),
    ("pg_dump company > out.sql", "database_client"),
    ("pg_dumpall", "database_client"),
    ("pg_restore -d company dump", "database_client"),
    ("sudo -u postgres psql", "database_client"),
    ("env FOO=1 psql", "database_client"),
    ("echo hi && psql", "database_client"),
    ("ls | xargs psql -c", "database_client"),
    ("/usr/bin/psql -c 'select 1'", "database_client"),
    (r'"C:\Program Files\PostgreSQLin\psql.exe" -c x', "database_client"),
    ("PSQL -c x", "database_client"),
    ("bash -c 'psql -c x'", "database_client"),
    ('sh -c "echo ok; psql"', "database_client"),
    ("bash -lc 'bash -c \"psql\"'", "database_client"),
    ("echo $(psql -c x)", "database_client"),
    ('echo "$(psql -c x)"', "database_client"),
    ("echo `psql -c x`", "database_client"),
    ("(psql -c x)", "database_client"),
    ("true\npsql", "database_client"),
    ("if true; then psql; fi", "database_client"),
    ("{ psql; }", "database_client"),
    ("eval 'psql -c x'", "database_client"),
    ('powershell -Command "psql -c x"', "database_client"),
    ("pwsh -c psql", "database_client"),
    ("cmd /c psql", "database_client"),
    # credential_file
    ("cat .env", "credential_file"),
    ("cat ./backend/.env", "credential_file"),
    ("cat .env.local", "credential_file"),
    ("head -n1 '.env'", "credential_file"),
    ("cat < .env", "credential_file"),
    ("cat .ENV", "credential_file"),
    ("less ~/.ssh/id_rsa", "credential_file"),
    ("cp id_ed25519 /tmp/x", "credential_file"),
    ("cat server.pem", "credential_file"),
    ("cat ~/.pgpass", "credential_file"),
    ("cat ~/.netrc", "credential_file"),
    ("cat ~/.git-credentials", "credential_file"),
    ("cat ~/.aws/credentials", "credential_file"),
    ("cat ~/.config/neonctl/credentials.json", "credential_file"),
    ("cat ~/.claude.json", "credential_file"),
    (r"type D:\Blastproof\backend\.env", "credential_file"),
    (r"cat D:\x\.env", "credential_file"),
    ("ls *.key", "credential_file"),
    # hook_settings
    ("echo '{}' > .claude/settings.local.json", "hook_settings"),
    (r"echo {} > .claude\settings.local.json", "hook_settings"),
    ("sed -i s/x/y/ .claude/settings.json", "hook_settings"),
    ("cd .claude && echo {} > settings.local.json", "hook_settings"),
    ("cat .claude/settings.json", "hook_settings"),
    # hook_bypass
    ("export CLAUDE_X=1; echo disableAllHooks", "hook_bypass"),
    ("claude --bare -p hi", "hook_bypass"),
    ("claude -p hi --settings x.json", "hook_bypass"),
    ("claude --settings=x.json", "hook_bypass"),
    ("claude --setting-sources user", "hook_bypass"),
    ("nohup claude --bare &", "hook_bypass"),
    # terraform
    ("terraform apply", "terraform"),
    ("terraform apply -auto-approve", "terraform"),
    ("terraform -chdir=infra destroy", "terraform"),
    ("TF_LOG=1 terraform destroy", "terraform"),
    # git_force_push
    ("git push -f", "git_force_push"),
    ("git push --force origin main", "git_force_push"),
    ("git push origin main --force-with-lease", "git_force_push"),
    ("git push --force-if-includes", "git_force_push"),
    ("git push origin +main", "git_force_push"),
    ("git push -fu origin main", "git_force_push"),
    ("git -C repo push -f", "git_force_push"),
    ("git -c user.name=x push --force", "git_force_push"),
    # rm_outside_workspace
    ("rm -rf /", "rm_outside_workspace"),
    ("rm -rf ~", "rm_outside_workspace"),
    ("rm -rf ~/projects", "rm_outside_workspace"),
    ("rm -rf $HOME", "rm_outside_workspace"),
    ("rm -rf $HOME/x", "rm_outside_workspace"),
    ("rm -fr ..", "rm_outside_workspace"),
    ("rm -rf ../other", "rm_outside_workspace"),
    ("rm -rf ./build/../../x", "rm_outside_workspace"),
    ("rm -r -f /tmp/x", "rm_outside_workspace"),
    ("rm --recursive --force /tmp/x", "rm_outside_workspace"),
    ("rm -Rf /tmp/x", "rm_outside_workspace"),
    ("rm -rf build /etc", "rm_outside_workspace"),
    ("rm -rf .", "rm_outside_workspace"),
    ("rm -rf -- /tmp/x", "rm_outside_workspace"),
    ("rm -rf D:/other", "rm_outside_workspace"),
    ("rm -rf /d/other", "rm_outside_workspace"),
    ("rm -rf %USERPROFILE%", "rm_outside_workspace"),
    ("sudo rm -rf /var/lib", "rm_outside_workspace"),
    ("Remove-Item -Recurse -Force C:/x", "rm_outside_workspace"),
    ("Remove-Item -Force -Recurse ../x", "rm_outside_workspace"),
    ("Remove-Item -rec -fo C:/x", "rm_outside_workspace"),
    ("rm -Recurse -Force C:/x", "rm_outside_workspace"),
    ("ri -r -fo $env:USERPROFILE", "rm_outside_workspace"),
    ("rd /s /q C:/x", "rm_outside_workspace"),
    # unparseable and too long
    ("echo 'unterminated", "unparseable_command"),
    ('foo "unterminated', "unparseable_command"),
]


@pytest.mark.parametrize(("command", "rule"), DENIED_COMMANDS)
def test_denied_commands(command: str, rule: str) -> None:
    result = bash(command)
    assert (result.decision, result.rule) == ("deny", rule), command
    assert result.reason == REASONS[rule]


def test_a_command_over_100000_characters_is_refused() -> None:
    result = bash("echo " + "a" * 100_001)
    assert (result.decision, result.rule) == ("deny", "too_long")
    assert bash("echo " + "a" * 1000).decision == "allow"


ALLOWED_COMMANDS = [
    "ls -la",
    "git push",
    "git push origin main",
    "git status",
    "git log --oneline -5",
    "git commit -m 'psql is a client'",
    "rm -rf ./build",
    "rm -rf build/cache",
    "rm -rf ./a/../b",
    "rm file.txt",
    "rm -r build",
    "rm -f build",
    r"rm -rf .\build",
    "rm -rf D:/My\\ Workplace/blastproof-demo/build",
    "cat README.md",
    "grep -r foo src",
    "grep -r psql_notes docs",
    "terraform plan",
    "terraform init",
    "terraform fmt",
    "npm test",
    "python -m pytest",
    "uv run ruff check .",
    "echo psqlish",
    "cat package.json",
    "ls .environment",
    "cat .envrc",
    "claude -p 'hello'",
    "echo hello | wc -c",
    "git add . && git commit -m x",
    "cat <<'EOF'\nhello\nEOF",
    "env",
    "Get-ChildItem env:",
    "sudo ls",
    "xargs echo",
]


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allowed_commands(command: str) -> None:
    result = bash(command)
    assert result.decision == "allow", (command, result.rule)
    assert result.rule is None


POWERSHELL_DENIED = [
    ('& "C:\\Program Files\\PostgreSQL\\16\\bin\\psql.exe" -c x', "database_client"),
    ("& psql", "database_client"),
    ("Get-Content .env", "credential_file"),
    ("Set-Content .claude\\settings.local.json '{}'", "hook_settings"),
    ("Remove-Item -Recurse -Force C:\\Windows\\Temp\\x", "rm_outside_workspace"),
    ("Remove-Item -Recurse -Force $env:TEMP", "rm_outside_workspace"),
    ("rm -r -Force ..\\x", "rm_outside_workspace"),
    ("git push --force", "git_force_push"),
    ("iex 'psql'", "database_client"),
    ("pwsh -NoProfile -Command psql", "database_client"),
    ("claude --bare", "hook_bypass"),
]


@pytest.mark.parametrize(("command", "rule"), POWERSHELL_DENIED)
def test_powershell_denied(command: str, rule: str) -> None:
    assert rule_of("PowerShell", {"command": command}) == rule


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem",
        r"Remove-Item -Recurse -Force .\build",
        r"Remove-Item -Recurse -Force 'D:\My Workplace\blastproof-demo\build'",
        "Remove-Item file.txt",
        "Remove-Item -Recurse build",
        "Get-Content README.md",
    ],
)
def test_powershell_allowed(command: str) -> None:
    assert rule_of("PowerShell", {"command": command}) is None


def test_encoded_powershell_command_is_decoded() -> None:
    encoded = base64.b64encode("psql -c x".encode("utf-16-le")).decode()
    assert rule_of("PowerShell", {"command": f"powershell -EncodedCommand {encoded}"}) == (
        "database_client"
    )
    assert rule_of("Bash", {"command": "powershell -enc !!!notbase64"}) == "unparseable_command"


def test_nested_shells_stop_at_a_fixed_depth() -> None:
    command = "psql"
    for _ in range(hook_rules.MAX_NESTING + 2):
        command = "bash -c " + repr(command)
    # Too deep to look into: refused rather than waved through.
    assert rule_of("Bash", {"command": command}) in {"unparseable_command", "database_client"}


def test_a_wrapper_does_not_hide_the_program() -> None:
    assert rule_of("Bash", {"command": "sudo -n -E psql"}) == "database_client"
    assert rule_of("Bash", {"command": "time git push -f"}) == "git_force_push"


def test_a_very_long_wrapped_command_is_fast() -> None:
    started = time.monotonic()
    bash("sudo " + " ".join(f"a{i}" for i in range(20_000)))
    assert time.monotonic() - started < 5


# --- Monitor ----------------------------------------------------------------------------------


def test_monitor_command_is_checked_and_websocket_is_allowed() -> None:
    assert rule_of("Monitor", {"command": "psql -c x"}) == "database_client"
    assert rule_of("Monitor", {"command": "tail -f app.log"}) is None
    assert rule_of("Monitor", {"ws": "wss://example.com/stream"}) is None


# --- file tools -------------------------------------------------------------------------------

FILE_DENIED = [
    ("Read", {"file_path": r"D:\My Workplace\Blastproof\backend\.env"}, "credential_file"),
    ("Read", {"file_path": "../blastproof/backend/.env"}, "credential_file"),
    ("Read", {"file_path": "/d/My Workplace/Blastproof/.env.local"}, "credential_file"),
    ("Read", {"file_path": "~/.pgpass"}, "credential_file"),
    ("Read", {"file_path": "~/.ssh/id_rsa"}, "credential_file"),
    ("Read", {"file_path": "C:\\Users\\a\\.ssh\\config"}, "credential_file"),
    ("Read", {"file_path": "certs/server.PEM"}, "credential_file"),
    ("Read", {"file_path": "~/.claude.json"}, "credential_file"),
    ("Read", {"file_path": ".claude/settings.json"}, "hook_settings"),
    ("Read", {"file_path": ".claude/settings.local.json"}, "hook_settings"),
    ("Edit", {"file_path": ".claude/settings.json"}, "hook_settings"),
    ("Edit", {"file_path": ".CLAUDE\\Settings.json"}, "hook_settings"),
    ("Edit", {"file_path": "backend/.env"}, "credential_file"),
    ("Write", {"file_path": ".claude/settings.local.json"}, "hook_settings"),
    ("Write", {"file_path": "settings.local.json"}, "hook_settings"),
    ("Write", {"file_path": ".env"}, "credential_file"),
    ("NotebookEdit", {"notebook_path": "../.env"}, "credential_file"),
    ("NotebookEdit", {"file_path": "../.env"}, "credential_file"),
    ("Grep", {"pattern": "DATABASE_URL", "path": "..", "glob": ".env*"}, "credential_file"),
    ("Grep", {"pattern": "x", "path": "~/.ssh"}, "credential_file"),
    ("Grep", {"pattern": "x", "glob": "*.pem"}, "credential_file"),
    ("Glob", {"pattern": "**/.env"}, "credential_file"),
    ("Glob", {"pattern": "**/.env.*"}, "credential_file"),
    ("Glob", {"pattern": "**/id_rsa*"}, "credential_file"),
    ("Glob", {"pattern": "*", "path": ".claude/settings.json"}, "hook_settings"),
]


@pytest.mark.parametrize(("tool", "tool_input", "rule"), FILE_DENIED)
def test_file_tools_denied(tool: str, tool_input: dict[str, Any], rule: str) -> None:
    result = verdict(tool, tool_input)
    assert (result.decision, result.rule) == ("deny", rule), (tool, tool_input)
    assert result.reason == REASONS[rule]


FILE_ALLOWED = [
    ("Read", {"file_path": "README.md"}),
    ("Read", {"file_path": r"D:\My Workplace\blastproof-demo\CLAUDE.md"}),
    ("Read", {"file_path": ".mcp.json"}),
    ("Read", {"file_path": "src/environment.py"}),
    ("Read", {"file_path": "docs/keyboard.md"}),
    ("Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "notes/todo.md", "content": "x"}),
    ("NotebookEdit", {"notebook_path": "analysis.ipynb"}),
    ("Grep", {"pattern": "foo", "path": "src", "glob": "*.py"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Edit", {"file_path": 5}),
]


@pytest.mark.parametrize(("tool", "tool_input"), FILE_ALLOWED)
def test_file_tools_allowed(tool: str, tool_input: dict[str, Any]) -> None:
    assert verdict(tool, tool_input).decision == "allow"


def test_an_unknown_tool_is_allowed() -> None:
    assert verdict("WebFetch", {"url": "https://example.com/.env"}).decision == "allow"
    assert verdict("mcp__blastproof__query_database", {"sql": "select 1"}).decision == "allow"


def test_a_missing_command_field_is_allowed() -> None:
    assert verdict("Bash", {}).decision == "allow"


# --- paths ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "cwd", "expected"),
    [
        ("a/b.txt", WIN_CWD, "d:/my workplace/blastproof-demo/a/b.txt"),
        (r"..\x", WIN_CWD, "d:/my workplace/x"),
        ("../../../..", WIN_CWD, "d:/"),
        (r"C:\Users\Me\.ENV", WIN_CWD, "c:/users/me/.env"),
        ("/d/My Workplace/x", WIN_CWD, "d:/my workplace/x"),
        ("//d/My Workplace/x", WIN_CWD, "d:/my workplace/x"),
        ("D://a//b/./c", WIN_CWD, "d:/a/b/c"),
        ("src/../lib", POSIX_CWD, "/home/me/demo/lib"),
        ("/etc/../tmp/x", POSIX_CWD, "/tmp/x"),
        ("/A/B", POSIX_CWD, "/A/B"),
        ("~/x/../y", POSIX_CWD, "~/y"),
        ("$HOME/x", POSIX_CWD, "$HOME/x"),
    ],
)
def test_normalize(path: str, cwd: str, expected: str) -> None:
    assert normalize(path, cwd) == expected


def test_rm_uses_the_project_dir_not_the_cwd() -> None:
    sub = WIN_CWD + r"\sub"
    assert evaluate("Bash", {"command": "rm -rf ../x"}, sub, WIN_CWD).decision == "allow"
    assert evaluate("Bash", {"command": "rm -rf ../../x"}, sub, WIN_CWD).rule == (
        "rm_outside_workspace"
    )
    assert evaluate("Bash", {"command": "rm -rf ../x"}, POSIX_CWD, None).rule == (
        "rm_outside_workspace"
    )


def test_rm_is_case_insensitive_on_windows_only() -> None:
    upper = r"rm -rf D:/MY\ WORKPLACE/BLASTPROOF-DEMO/build"
    assert bash(upper).decision == "allow"
    posix = evaluate("Bash", {"command": "rm -rf /HOME/ME/DEMO/x"}, POSIX_CWD, POSIX_CWD)
    assert posix.rule == "rm_outside_workspace"


# --- splitting --------------------------------------------------------------------------------


def test_split_commands() -> None:
    assert split_commands("a b; c && d || e | f & g", "bash") == [
        ["a", "b"],
        ["c"],
        ["d"],
        ["e"],
        ["f"],
        ["g"],
    ]
    assert split_commands("a\nb", "bash") == [["a"], ["b"]]
    assert split_commands('echo "a;b"', "bash") == [["echo", "a;b"]]
    assert split_commands(r"cat D:\x\y.txt", "powershell") == [["cat", r"D:\x\y.txt"]]
    with pytest.raises(hook_rules.Unparseable):
        split_commands("echo 'x", "bash")


# --- the incident replays ---------------------------------------------------------------------


def hook_scenarios() -> list[HookScenario]:
    found: list[HookScenario] = []
    for path in sorted((Path(__file__).parent.parent / "evals" / "scenarios").glob("*.yaml")):
        found += [s for s in load_scenario_file(path).scenarios if isinstance(s, HookScenario)]
    return found


def test_there_are_hook_scenarios() -> None:
    assert len(hook_scenarios()) >= 8


@pytest.mark.parametrize("scenario", hook_scenarios(), ids=lambda s: s.id)
def test_every_hook_scenario_gets_its_expected_decision(scenario: HookScenario) -> None:
    result = evaluate(scenario.tool_name, scenario.tool_input, WIN_CWD, WIN_CWD)
    assert result.decision == scenario.expected_decision, scenario.id


# --- the reasons ------------------------------------------------------------------------------


def test_the_reason_texts_are_fixed() -> None:
    assert REASONS["database_client"] == (
        "Blocked: direct database clients are not allowed. "
        "Use the blastproof MCP tools (query_database, list_tables, describe_table) instead."
    )
    assert REASONS["credential_file"] == (
        "Blocked: this would read or change a credential file. "
        "Agents get no database credentials; use the blastproof MCP tools."
    )
    assert REASONS["hook_settings"] == (
        "Blocked: the Claude Code settings that hold this guardrail "
        "cannot be read or changed by the agent."
    )
    assert REASONS["hook_bypass"] == (
        "Blocked: starting Claude Code without its hooks, or with other settings, "
        "would skip Blastproof's guardrails."
    )
    assert REASONS["terraform"] == (
        "Blocked: terraform apply and destroy change real infrastructure; ask a person."
    )
    assert REASONS["git_force_push"] == (
        "Blocked: force-pushing can destroy other people's commits. Push normally."
    )
    assert REASONS["rm_outside_workspace"] == (
        "Blocked: a recursive forced delete is only allowed on a literal path inside the "
        "workspace folder."
    )
    assert REASONS["unparseable_command"] == (
        "Blocked: the command could not be parsed safely. Write it more simply."
    )
    assert REASONS["too_long"] == (
        "Blocked: the command is too long to check. Split it into smaller steps."
    )


def test_a_reason_never_repeats_the_input() -> None:
    secret = "hunter2-planted"
    for command in (f"psql -W {secret}", f"cat .env {secret}", f"git push --force {secret}"):
        result = bash(command)
        assert result.decision == "deny"
        assert secret not in result.reason


def test_allow_carries_the_no_rule_text() -> None:
    result = bash("ls")
    assert (result.decision, result.rule, result.reason) == ("allow", None, "No rule matched.")


# --- the summary ------------------------------------------------------------------------------


def test_summary_scrubs_secrets() -> None:
    key = "bp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U_v"
    command = (
        "psql postgres://admin:s3cr3tpw@db.example.com/co --password hunter2 "
        "PGPASSWORD=topsecret API_TOKEN='a b c' MY_SECRET=zzz STRIPE_KEY=sk_live_1 "
        f"-H 'Authorization: Bearer {key}' jane.doe@example.com +1 (415) 555-0132"
    )
    text = summarize("Bash", {"command": command})
    for leaked in (
        "s3cr3tpw",
        "hunter2",
        "topsecret",
        "a b c",
        "zzz",
        "sk_live_1",
        key,
        "jane.doe@example.com",
        "555-0132",
    ):
        assert leaked not in text, leaked
    assert text.startswith("Bash: psql postgres://admin:[masked]@db.example.com/co")


def test_summary_names_paths_for_file_tools_and_drops_bodies() -> None:
    text = summarize(
        "Edit",
        {"file_path": "src/app.py", "old_string": "secret-old", "new_string": "secret-new"},
    )
    assert text == "Edit: src/app.py"
    grep = summarize("Grep", {"pattern": "hunter2", "path": "src", "glob": "*.py"})
    assert grep == "Grep: src *.py"


def test_summary_is_capped_at_500_characters() -> None:
    assert len(summarize("Bash", {"command": "echo " + "x" * 5000})) == 500
    assert summarize("Monitor", {"ws": "wss://x"}) == "Monitor: (no command)"
    assert summarize("WebFetch", {"url": "https://x"}) == "WebFetch: (not summarised)"
