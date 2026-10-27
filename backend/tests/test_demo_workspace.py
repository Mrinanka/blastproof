"""Step 7: the demo workspace for Claude Code holds no company database credential."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "claude_hook" / "workspace.py"
KEY = "bp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U_v"  # 43 characters after the prefix
BASE_URL = "http://localhost:8000"
MARKER = "PostgreSQL://"
SECRET = "hunter2-planted-secret"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("workspace_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ws_mod = load_module()
REPO = Path("D:/My Workplace/Blastproof")


def run_cli(
    *args: str, stdin: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env if env is not None else clean_env(),
        timeout=60,
    )


def clean_env(**extra: str) -> dict[str, str]:
    """The test process's environment minus anything the scan would flag."""
    env = {k: v for k, v in os.environ.items() if not ws_mod.matches(k) and not ws_mod.matches(v)}
    env.update(extra)
    return env


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "demo"


def created(workspace: Path) -> Path:
    result = run_cli("create", str(workspace), "--key-stdin", stdin=KEY + "\n")
    assert result.returncode == 0, result.stderr
    return workspace


# --- paths and rules ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"D:\My Workplace\Blastproof", "//d/My Workplace/Blastproof"),
        ("d:/x/y", "//d/x/y"),
        ("/home/me/repo", "//home/me/repo"),
        (r"C:\Users\a b\repo", "//c/Users/a b/repo"),
    ],
)
def test_claude_path(path: str, expected: str) -> None:
    assert ws_mod.claude_path(path) == expected


def test_deny_rules_exact_list() -> None:
    assert ws_mod.deny_rules(REPO) == [
        "Read(//d/My Workplace/Blastproof/**)",
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
        "Bash(docker)",
        "Bash(docker *)",
        "PowerShell(docker)",
        "PowerShell(docker *)",
    ]


# --- generated files ---------------------------------------------------------------------------


def test_generated_files_shape() -> None:
    files = ws_mod.build_files(REPO, BASE_URL, KEY)
    assert set(files) == {
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".mcp.json",
        "CLAUDE.md",
    }
    settings = json.loads(files[".claude/settings.json"])
    assert settings == {
        "permissions": {
            "deny": ws_mod.deny_rules(REPO),
            "blockReadsOutsideWorkingDirectories": True,
        },
        "disableClaudeAiConnectors": True,
        "hooks": {"PreToolUse": [ws_mod.hook_entry(REPO, BASE_URL, sys.executable)]},
    }
    local = json.loads(files[".claude/settings.local.json"])
    assert local == {"env": {"BLASTPROOF_AGENT_KEY": KEY}, "enabledMcpjsonServers": ["blastproof"]}
    mcp = json.loads(files[".mcp.json"])
    server = mcp["mcpServers"]["blastproof"]
    assert server["url"] == "http://localhost:8000/mcp"
    assert server["headers"]["Authorization"] == "Bearer ${BLASTPROOF_AGENT_KEY}"
    for name, text in files.items():
        if name != ".claude/settings.local.json":
            assert KEY not in text


@pytest.mark.parametrize("name", ["NEON_API_KEY", "NEON_API_KEY_DEV"])
def test_scan_flags_a_neon_api_key_variable(name: str, tmp_path: Path) -> None:
    found = ws_mod.scan(tmp_path, {name: "napi_x", "PATH": "/bin"})
    assert [(f.kind, f.where) for f in found] == [("env", name)]


def test_no_generated_file_matches_a_scan_pattern() -> None:
    for name, text in ws_mod.build_files(REPO, BASE_URL, KEY).items():
        assert not ws_mod.matches(text), name
        assert not ws_mod.matches(name), name


# --- the hook entry (step 18) ------------------------------------------------------------------

MATCHER = "Bash|PowerShell|Monitor|Edit|Write|NotebookEdit|Read|Grep|Glob"


def hook_of(settings: dict) -> dict:
    [group] = settings["hooks"]["PreToolUse"]
    assert group["matcher"] == MATCHER
    [hook] = group["hooks"]
    return hook


def test_the_hook_entry_blocks_on_failure_and_outlasts_two_attempts() -> None:
    files = ws_mod.build_files(REPO_ROOT, BASE_URL, KEY, python="/py/python")
    hook = hook_of(json.loads(files[".claude/settings.json"]))
    assert hook["type"] == "command"
    assert hook["command"] == "/py/python"
    assert hook["onFailure"] == "block"
    # Two attempts, one second between them, and Python's start-up.
    assert hook["timeout"] >= 2 * ws_mod.HOOK_HTTP_TIMEOUT + 1 + 2
    script, flag, url = hook["args"]
    assert Path(script).is_absolute() and Path(script).is_file()
    assert Path(script) == REPO_ROOT / "claude_hook" / "pre_tool_use.py"
    assert (flag, url) == ("--url", BASE_URL)


def test_the_hook_entry_defaults_to_the_current_python_and_holds_no_key() -> None:
    files = ws_mod.build_files(REPO_ROOT, BASE_URL, KEY)
    hook = hook_of(json.loads(files[".claude/settings.json"]))
    assert hook["command"] == sys.executable
    for name, text in files.items():
        if name != ".claude/settings.local.json":
            assert KEY not in text, name
    assert "bp_" not in json.dumps(hook)


def test_the_example_settings_have_the_same_shape() -> None:
    example = json.loads((REPO_ROOT / "claude_hook" / "settings.example.json").read_text("utf-8"))
    real = ws_mod.hook_entry(REPO_ROOT, BASE_URL, "/py/python")
    [group] = example["hooks"]["PreToolUse"]
    [hook] = group["hooks"]
    assert group["matcher"] == real["matcher"]
    assert set(hook) == set(real["hooks"][0])
    assert hook["onFailure"] == "block"
    assert hook["timeout"] == ws_mod.HOOK_TIMEOUT
    assert hook["args"][1:] == ["--url", BASE_URL]


def test_create_writes_the_hook_and_accepts_python(workspace: Path) -> None:
    args = ("create", str(workspace), "--key-stdin", "--python", "/py/python")
    result = run_cli(*args, stdin=KEY + chr(10))
    assert result.returncode == 0, result.stderr
    settings = json.loads((workspace / ".claude" / "settings.json").read_text("utf-8"))
    assert hook_of(settings)["command"] == "/py/python"
    assert KEY not in (workspace / ".claude" / "settings.json").read_text("utf-8")


def test_the_hook_still_leaves_the_workspace_clean_of_credentials(workspace: Path) -> None:
    created(workspace)
    assert ws_mod.scan(workspace, {}) == []


# --- create ------------------------------------------------------------------------------------


def test_create_happy_path_never_prints_the_key(workspace: Path) -> None:
    result = run_cli("create", str(workspace), "--key-stdin", stdin=KEY + "\n")
    assert result.returncode == 0
    assert KEY not in result.stdout + result.stderr
    assert (workspace / ".mcp.json").is_file()
    local = json.loads((workspace / ".claude" / "settings.local.json").read_text("utf-8"))
    assert local["env"] == {"BLASTPROOF_AGENT_KEY": KEY}


def test_create_without_key_writes_a_placeholder(workspace: Path) -> None:
    assert run_cli("create", str(workspace)).returncode == 0
    local = json.loads((workspace / ".claude" / "settings.local.json").read_text("utf-8"))
    assert local["env"] == {"BLASTPROOF_AGENT_KEY": ""}


def test_create_refuses_a_workspace_inside_the_repo() -> None:
    inside = REPO_ROOT / "claude_hook" / "never-created"
    result = run_cli("create", str(inside))
    assert result.returncode != 0
    assert "outside the repo" in result.stderr
    assert not inside.exists()


def test_create_refuses_a_workspace_that_contains_the_repo() -> None:
    result = run_cli("create", str(REPO_ROOT.parent))
    assert result.returncode != 0
    assert "would contain the repo" in result.stderr
    assert not (REPO_ROOT.parent / ".mcp.json").exists()


def test_create_refuses_a_non_empty_folder_without_force(workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "notes.txt").write_text("mine", "utf-8")
    result = run_cli("create", str(workspace))
    assert result.returncode != 0
    assert [p.name for p in workspace.iterdir()] == ["notes.txt"]


def test_force_rewrites_only_its_own_files(workspace: Path) -> None:
    created(workspace)
    (workspace / "notes.txt").write_text("mine", "utf-8")
    (workspace / "CLAUDE.md").write_text("old", "utf-8")
    assert run_cli("create", str(workspace), "--force").returncode == 0
    assert (workspace / "notes.txt").read_text("utf-8") == "mine"
    assert (workspace / "CLAUDE.md").read_text("utf-8") != "old"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "\n",
        "sk_" + KEY[3:] + "\n",
        KEY[:-1] + "\n",
        KEY + "x\n",
        KEY + "\n" + KEY + "\n",
        KEY[:-1] + "!\n",
    ],
)
def test_create_refuses_a_bad_key_and_never_prints_it(workspace: Path, bad: str) -> None:
    result = run_cli("create", str(workspace), "--key-stdin", stdin=bad)
    assert result.returncode != 0
    assert not workspace.exists()
    shown = result.stdout + result.stderr
    assert KEY[:-1] not in shown
    assert bad.strip() == "" or bad.strip() not in shown


# --- scan --------------------------------------------------------------------------------------


def test_scan_clean_after_create(workspace: Path) -> None:
    created(workspace)
    result = run_cli("check", str(workspace))
    assert result.returncode == 0, result.stdout


def test_scan_finds_marker_in_nested_file(workspace: Path) -> None:
    created(workspace)
    (workspace / "a" / "b").mkdir(parents=True)
    (workspace / "a" / "b" / "c.txt").write_text(f"{MARKER}u:{SECRET}@h/db", "utf-8")
    result = run_cli("check", str(workspace))
    assert result.returncode == 1
    assert "c.txt" in result.stdout
    assert SECRET not in result.stdout + result.stderr


def test_scan_finds_marker_in_a_file_name(workspace: Path) -> None:
    created(workspace)
    (workspace / "postgres-backup.txt").write_text("x", "utf-8")
    assert run_cli("check", str(workspace)).returncode == 1


def test_scan_finds_marker_in_a_binary_file(workspace: Path) -> None:
    created(workspace)
    (workspace / "blob.bin").write_bytes(b"\x00\xff\x00postgres\x00\x01")
    assert run_cli("check", str(workspace)).returncode == 1


def test_scan_finds_an_env_value_without_printing_it(workspace: Path) -> None:
    created(workspace)
    env = clean_env(SOME_SETTING=f"{MARKER}u:{SECRET}@h/db")
    result = run_cli("check", str(workspace), env=env)
    assert result.returncode == 1
    assert "SOME_SETTING" in result.stdout
    assert SECRET not in result.stdout + result.stderr


def test_scan_finds_an_env_name(workspace: Path) -> None:
    created(workspace)
    result = run_cli("check", str(workspace), env=clean_env(PGPASSWORD="x"))
    assert result.returncode == 1
    assert "PGPASSWORD" in result.stdout


def test_scan_treats_a_symlink_as_a_finding(workspace: Path, tmp_path: Path) -> None:
    created(workspace)
    target = tmp_path / "target.txt"
    target.write_text("harmless", "utf-8")
    try:
        (workspace / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    result = run_cli("check", str(workspace))
    assert result.returncode == 1
    assert "link.txt" in result.stdout


def test_scan_symlink_finding_without_privileges(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.mkdir()
    (workspace / "link.txt").write_text("harmless", "utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link.txt")
    findings = ws_mod.scan(workspace, {})
    assert findings == [ws_mod.Finding("symlink", "link.txt")]


def test_scan_missing_folder_exits_2(tmp_path: Path) -> None:
    assert run_cli("check", str(tmp_path / "nope")).returncode == 2
