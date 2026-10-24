"""SEC-01, SEC-02, SEC-03: the create-user and create-agent commands.

Runs `python -m app.cli` as a subprocess from an empty folder, so it never reads backend/.env and
sees only the database URL given here.
"""

import hashlib
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    BACKEND_DIR,
    BLASTPROOF_OWNER_URL,
    TEST_PASSWORD,
    AuthDb,
    asyncpg_url,
    bearer,
    login,
    on_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

KEY_PATTERN = re.compile(r"bp_[A-Za-z0-9_-]{43}")


def run_cli(
    tmp_path: Path, *args: str, database_url: str | None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"BLASTPROOF_DATABASE_URL", "JWT_SECRET", "PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(BACKEND_DIR)
    if database_url is not None:
        env["BLASTPROOF_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        input=stdin,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


@pytest.fixture
def db_url(auth_db: AuthDb) -> str:
    return asyncpg_url(auth_db.app_url)


def new_email() -> str:
    return f"cli-{uuid.uuid4().hex[:10]}@Example.com"


def create_user(
    tmp_path: Path,
    db_url: str | None,
    email: str,
    role: str = "member",
    password: str = TEST_PASSWORD,
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        tmp_path,
        "create-user",
        "--email",
        email,
        "--display-name",
        "Cli User",
        "--role",
        role,
        "--password-stdin",
        database_url=db_url,
        stdin=password + "\n",
    )


def test_create_user_then_log_in(
    tmp_path: Path, db_url: str, auth_db: AuthDb, auth_client: TestClient
) -> None:
    email = new_email()
    result = create_user(tmp_path, db_url, email, role="admin")
    assert result.returncode == 0, result.stderr
    assert "User created" in result.stdout
    assert TEST_PASSWORD not in result.stdout + result.stderr
    [(stored_email, role, password_hash)] = auth_db.execute(
        "SELECT email, role, password_hash FROM users WHERE lower(email) = lower(%s)", (email,)
    )
    assert (stored_email, role) == (email.lower(), "admin")
    assert password_hash.startswith("$argon2id$")
    assert login(auth_client, email, TEST_PASSWORD).status_code == 200


def test_create_agent_shows_the_key_once_and_stores_only_its_hash(
    tmp_path: Path, db_url: str, auth_db: AuthDb, auth_client: TestClient
) -> None:
    owner = auth_db.make_user()
    result = run_cli(
        tmp_path,
        "create-agent",
        "--owner-email",
        owner.email,
        "--name",
        "claude-code",
        database_url=db_url,
    )
    assert result.returncode == 0, result.stderr
    [key] = KEY_PATTERN.findall(result.stdout)
    assert result.stdout.count(key) == 1
    assert key not in result.stderr
    [(key_hash, key_prefix, owner_id)] = auth_db.execute(
        "SELECT api_key_hash, key_prefix, owner_id FROM agents WHERE name = 'claude-code'"
        " AND owner_id = %s",
        (owner.id,),
    )
    assert key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key_prefix == key[:8]
    assert owner_id == owner.id
    assert auth_client.get("/test/agent", headers=bearer(key)).status_code == 200


def test_duplicate_email_fails_cleanly(tmp_path: Path, db_url: str) -> None:
    email = new_email()
    assert create_user(tmp_path, db_url, email).returncode == 0
    again = create_user(tmp_path, db_url, email.upper())
    assert again.returncode == 1
    assert "already exists" in again.stderr
    assert "Traceback" not in again.stderr


def test_short_password_is_refused_and_nothing_is_created(
    tmp_path: Path, db_url: str, auth_db: AuthDb
) -> None:
    email = new_email()
    result = create_user(tmp_path, db_url, email, password="short")
    assert result.returncode == 1
    assert "12 to 256 characters" in result.stderr
    assert "short" not in result.stdout
    assert auth_db.execute("SELECT 1 FROM users WHERE lower(email) = lower(%s)", (email,)) == []


def test_unknown_owner_fails_cleanly(tmp_path: Path, db_url: str) -> None:
    result = run_cli(
        tmp_path, "create-agent", "--owner-email", new_email(), "--name", "x", database_url=db_url
    )
    assert result.returncode == 1
    assert "No active user has that email" in result.stderr
    assert not KEY_PATTERN.search(result.stdout + result.stderr)
    assert "Traceback" not in result.stderr


def test_password_is_never_an_argument(tmp_path: Path, db_url: str) -> None:
    result = run_cli(
        tmp_path,
        "create-user",
        "--email",
        new_email(),
        "--display-name",
        "x",
        "--password",
        TEST_PASSWORD,
        database_url=db_url,
    )
    assert result.returncode == 2  # argparse: unknown argument


def test_missing_database_url_is_a_clear_error(tmp_path: Path) -> None:
    result = create_user(tmp_path, None, new_email())
    assert result.returncode == 1
    assert "BLASTPROOF_DATABASE_URL is not set" in result.stderr
    assert "Traceback" not in result.stderr


def test_owner_role_is_refused_without_printing_the_password(
    tmp_path: Path, auth_db: AuthDb
) -> None:
    owner_url = asyncpg_url(on_database(BLASTPROOF_OWNER_URL, auth_db.name))
    result = create_user(tmp_path, owner_url, new_email())
    assert result.returncode == 1
    assert "must connect as blastproof_app" in result.stderr
    # The local owner password equals the role name; it must not appear after "user:".
    assert "blastproof_owner:blastproof_owner" not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr
