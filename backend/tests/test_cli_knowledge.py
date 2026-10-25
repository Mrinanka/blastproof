"""Step 11: knowledge-scan, knowledge-ingest and reindex print counts only (BE-59, BE-62)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import BACKEND_DIR, COMPANY_READER_URL, AuthDb, asyncpg_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("blastproof_db_reachable", "company_db_seeded"),
]

SECRET = "ultra-secret-business-rule-7731"
KEPT = {"BLASTPROOF_DATABASE_URL", "LOCAL_COMPANY_READER_URL", "COMPANY_CONNECTION_PROFILE"}


def run_cli(
    tmp_path: Path, auth_db: AuthDb, *args: str, database: bool = True
) -> subprocess.CompletedProcess[str]:
    """From an empty folder, so the command never reads backend/.env."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in KEPT | {"PYTHONPATH"}}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["COMPANY_CONNECTION_PROFILE"] = "local"
    env["LOCAL_COMPANY_READER_URL"] = COMPANY_READER_URL
    if database:
        env["BLASTPROOF_DATABASE_URL"] = asyncpg_url(auth_db.app_url)
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


@pytest.fixture(autouse=True)
def clean(auth_db: AuthDb):  # type: ignore[no-untyped-def]
    auth_db.execute("DELETE FROM knowledge_chunks")
    yield
    auth_db.execute("DELETE FROM knowledge_chunks")


def test_scan_ingest_and_reindex_print_counts_only(tmp_path: Path, auth_db: AuthDb) -> None:
    scan = run_cli(tmp_path, auth_db, "knowledge-scan")
    assert scan.returncode == 0, scan.stderr[-500:]
    assert re.fullmatch(r"Scanned the company schema: 11 table chunks in [\d.]+ s\.\n", scan.stdout)

    doc = tmp_path / "rules.md"
    doc.write_text(f"## table: users\n\n{SECRET}\n\n## Other\n\nMore text.\n", encoding="utf-8")
    ingest = run_cli(
        tmp_path, auth_db, "knowledge-ingest", "--file", str(doc), "--tables", "orders"
    )
    assert ingest.returncode == 0, ingest.stderr[-500:]
    assert re.fullmatch(r"Ingested 2 chunks in [\d.]+ s\.\n", ingest.stdout)
    rows = auth_db.execute(
        "SELECT source, table_names FROM knowledge_chunks WHERE source <> 'schema_scan'"
    )
    assert sorted((s, tuple(t)) for s, t in rows) == [
        ("rules.md", ("orders",)),
        ("rules.md", ("orders", "users")),
    ]

    auth_db.execute("DELETE FROM chunk_embeddings")
    reindex = run_cli(tmp_path, auth_db, "reindex")
    assert reindex.returncode == 0, reindex.stderr[-500:]
    assert re.fullmatch(r"Reindexed 13 chunks in [\d.]+ s\.\n", reindex.stdout)
    [(vectors,)] = auth_db.execute("SELECT count(*) FROM chunk_embeddings")
    assert vectors == 13

    for run in (scan, ingest, reindex):
        for stream in (run.stdout, run.stderr):
            assert SECRET not in stream
            assert COMPANY_READER_URL not in stream
            assert "postgresql" not in stream.lower()
            assert "blastproof_reader" not in stream


def test_a_source_name_can_be_given(tmp_path: Path, auth_db: AuthDb) -> None:
    doc = tmp_path / "x.md"
    doc.write_text("## A\n\nText.", encoding="utf-8")
    result = run_cli(
        tmp_path, auth_db, "knowledge-ingest", "--file", str(doc), "--source", "policy"
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert auth_db.execute("SELECT DISTINCT source FROM knowledge_chunks") == [("policy",)]


def test_a_missing_file_is_one_error_line(tmp_path: Path, auth_db: AuthDb) -> None:
    result = run_cli(tmp_path, auth_db, "knowledge-ingest", "--file", str(tmp_path / "nope.md"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip().splitlines()[-1] == "Error: The file could not be read."


def test_a_bad_table_tag_is_one_line_without_the_text(tmp_path: Path, auth_db: AuthDb) -> None:
    doc = tmp_path / "bad.md"
    doc.write_text(f"## table: bad name\n\n{SECRET}\n", encoding="utf-8")
    result = run_cli(tmp_path, auth_db, "knowledge-ingest", "--file", str(doc))
    assert result.returncode == 1
    lines = [ln for ln in result.stderr.splitlines() if ln.startswith("Error:")]
    assert len(lines) == 1
    assert SECRET not in result.stderr + result.stdout
    assert auth_db.execute("SELECT count(*) FROM knowledge_chunks") == [(0,)]


def test_a_missing_database_url_is_one_error_line(tmp_path: Path, auth_db: AuthDb) -> None:
    result = run_cli(tmp_path, auth_db, "reindex", database=False)
    assert result.returncode == 1
    assert "BLASTPROOF_DATABASE_URL is not set" in result.stderr
