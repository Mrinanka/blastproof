"""seed.py's ensure_role on Neon (D-077): an existing role is not altered with SUPERUSER words.

Neon's owner is not a superuser, so `ALTER ROLE ... NOSUPERUSER` fails with "permission denied
to alter role". A fake connection records the SQL, so no database is needed.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

DEMO = Path(__file__).resolve().parents[2] / "demo_company"


def load_seed() -> Any:
    sys.path.insert(0, str(DEMO))
    try:
        spec = importlib.util.spec_from_file_location("seed_under_test", DEMO / "seed.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(DEMO))


class FakeConn:
    def __init__(self, exists: bool, privileged: bool = False, groups: bool = False) -> None:
        self.exists, self.privileged, self.groups = exists, privileged, groups
        self.executed: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if sql.startswith("SELECT 1 FROM pg_roles"):
            return 1 if self.exists else None
        if "rolsuper OR" in sql:
            return self.privileged
        # the format() call: render it as Postgres would
        verb, role, password = args
        rest = sql.split("'%s ROLE %I ")[1].split("',")[0].replace("%L", f"'{password}'")
        return f"{verb} ROLE {role} {rest}"

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        return [{"rolname": "neon_superuser"}] if self.groups else []


URL = "postgresql://blastproof_reader:pw-1234@host/db"


async def run(conn: FakeConn) -> None:
    seed = load_seed()
    await seed.ensure_role(conn, "blastproof_reader", URL, "COMPANY_DB_READER_URL")


async def test_a_new_role_is_created_with_least_privilege() -> None:
    conn = FakeConn(exists=False)
    await run(conn)
    assert conn.executed == [
        "CREATE ROLE blastproof_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'pw-1234'"
    ]


async def test_an_existing_role_only_gets_its_password_reset() -> None:
    conn = FakeConn(exists=True)
    await run(conn)
    assert conn.executed == ["ALTER ROLE blastproof_reader LOGIN PASSWORD 'pw-1234'"]
    assert "SUPERUSER" not in conn.executed[0]


async def test_an_existing_privileged_role_is_refused() -> None:
    seed = load_seed()
    with pytest.raises(seed.TargetError, match="least privilege"):
        await seed.ensure_role(
            FakeConn(exists=True, privileged=True), "blastproof_reader", URL, "V"
        )


async def test_a_role_in_neon_superuser_is_still_refused() -> None:
    seed = load_seed()
    with pytest.raises(seed.TargetError, match="neon_superuser"):
        await seed.ensure_role(FakeConn(exists=True, groups=True), "blastproof_reader", URL, "V")
