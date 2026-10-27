"""Step 18 Done-when, automated: the real app under uvicorn and the real hook script (DW-B18).

Live, the hook denies `psql`, a read of `backend/.env` and an edit of `.claude/settings.json`, and
allows `ls`. After the server stops, shell and edit tools are denied, an ordinary read is allowed
and `.env` stays denied.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app
from tests.conftest import REPO_ROOT, AuthDb, TestAgent

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

SCRIPT = REPO_ROOT / "claude_hook" / "pre_tool_use.py"


class Live:
    """The app on a free loopback port, in a thread."""

    def __init__(self, settings: Settings) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=self.port, log_config=None
        )  # no log_config/log_level: uvicorn would change the shared "uvicorn.error" logger
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            assert self.thread.is_alive() and time.monotonic() < deadline, "the app did not start"
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=30)
        assert not self.thread.is_alive()


@pytest.fixture
def live(auth_settings: Settings) -> Iterator[Live]:
    app = Live(auth_settings)
    app.start()
    try:
        yield app
    finally:
        if app.thread.is_alive():
            app.stop()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "demo"
    path.mkdir()
    (path / "notes.md").write_text("hello", encoding="utf-8")
    return path


def hook(
    live: Live, agent: TestAgent, workspace: Path, tool: str, **tool_input: Any
) -> tuple[int, str]:
    event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(workspace)}
    env = {k: v for k, v in os.environ.items() if k != "BLASTPROOF_AGENT_KEY"}
    env["BLASTPROOF_AGENT_KEY"] = agent.key
    env["CLAUDE_PROJECT_DIR"] = str(workspace)
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--url", live.url, "--retry-pause", "0.2"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.stdout == ""
    return proc.returncode, proc.stderr


def test_live_then_stopped(live: Live, auth_db: AuthDb, workspace: Path) -> None:
    agent = auth_db.make_agent(auth_db.make_user().id)

    # Live: the rules decide.
    code, err = hook(live, agent, workspace, "Bash", command='psql "$DATABASE_URL" -c "select 1"')
    assert code == 2 and "blastproof MCP tools" in err
    code, err = hook(live, agent, workspace, "Read", file_path=str(REPO_ROOT / "backend" / ".env"))
    assert code == 2 and "credential file" in err
    for tool in ("Edit", "Write"):
        settings_file = str(workspace / ".claude" / "settings.json")
        code, err = hook(live, agent, workspace, tool, file_path=settings_file, content="x")
        assert code == 2 and "settings" in err, tool
    assert hook(live, agent, workspace, "Bash", command="ls") == (0, "")
    assert hook(live, agent, workspace, "Read", file_path=str(workspace / "notes.md")) == (0, "")

    rows = auth_db.execute(
        "SELECT decision, rule FROM hook_events WHERE agent_id = %s ORDER BY created_at",
        (agent.id,),
    )
    assert rows == [
        ("deny", "database_client"),
        ("deny", "credential_file"),
        ("deny", "hook_settings"),
        ("deny", "hook_settings"),
        ("allow", None),
        ("allow", None),
    ]

    # Stopped: shell and edit tools fail closed; reads of ordinary files still work.
    live.stop()
    code, err = hook(live, agent, workspace, "Bash", command="ls")
    assert code == 2 and "could not be reached" in err
    code, err = hook(live, agent, workspace, "Edit", file_path=str(workspace / "notes.md"))
    assert code == 2 and "could not be reached" in err
    assert hook(live, agent, workspace, "Read", file_path=str(workspace / "notes.md")) == (0, "")
    code, err = hook(live, agent, workspace, "Read", file_path=str(workspace / ".env"))
    assert code == 2 and "not readable" in err
