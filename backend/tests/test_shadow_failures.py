"""Step 10: every way a preview can fail ends in a reason code, a deleted copy and a quiet log."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app.config import DEFAULT_POLICY_FILE
from app.core.clock import FrozenClock
from app.logging import configure_logging
from app.services.classifier import classify
from app.services.neon import FakeNeonClient, NeonError, ShadowTarget
from app.services.policy import load_policy
from app.services.shadow import ShadowFailedError
from tests.conftest import (
    COMPANY_ADMIN_URL,
    COMPANY_WRITER_URL,
    company_url,
    fake_neon_client,
    leftover_shadow_databases,
    shadow_runner,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

CLOCK = FrozenClock(datetime(2026, 10, 1, tzinfo=UTC))
SECRET_LITERAL = "zzz-secret-literal-7731"


async def fail_with(sql: str, **runner_overrides: object) -> ShadowFailedError:
    runner = shadow_runner({"local": fake_neon_client(CLOCK)}, **runner_overrides)
    with pytest.raises(ShadowFailedError) as raised:
        await runner.run(
            action_id=uuid.uuid4(), profile="local", classification=classify(sql, now=CLOCK.now())
        )
    assert await leftover_shadow_databases() == []
    return raised.value


async def test_a_statement_error_gives_its_sqlstate_and_leaks_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()  # onto this test's captured stdout
    error = await fail_with(f"UPDATE users SET plan = '{SECRET_LITERAL}' WHERE id = 1")
    assert error.reason_code == "statement_error_23514"  # check_violation
    out = capsys.readouterr()
    assert "shadow_statement_error" in out.out  # the log is being captured at all
    for text in (out.out, out.err, str(error)):
        assert SECRET_LITERAL not in text and "violates" not in text
        assert "blastproof_writer" not in text and "users" not in text.replace("shadow", "")


async def test_a_slow_statement_hits_the_timeout() -> None:
    error = await fail_with(
        "DELETE FROM users WHERE id = 1 AND length((SELECT pg_sleep(20))::text) >= 0",
        timeout_seconds=1,
    )
    assert error.reason_code == "shadow_timeout"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM no_such_table WHERE id = 1",
        "DELETE FROM blastproof.applied_actions WHERE action_id IS NOT NULL",
        "DELETE FROM pg_temp.users WHERE id = 1",
        "DELETE FROM users WHERE id IN (SELECT id FROM pg_catalog.pg_class)",
        "DELETE FROM users WHERE id = 1 AND pg_catalog.length('a') > 0",
    ],
)
async def test_unknown_tables_and_other_schemas_are_refused(sql: str) -> None:
    assert (await fail_with(sql)).reason_code == "unknown_table"


@pytest.mark.parametrize(
    "sql", ["SELECT count(*) FROM users", "DROP TABLE users", "SELECT 1; SELECT 2"]
)
async def test_only_a_single_write_is_run(sql: str) -> None:
    assert (await fail_with(sql)).reason_code == "not_a_write"


async def test_a_policy_column_missing_from_the_database_fails(tmp_path: Path) -> None:
    rules = DEFAULT_POLICY_FILE.read_text(encoding="utf-8").replace(
        "important_columns:\n  users: [plan]", "important_columns:\n  users: [plan, no_such_column]"
    )
    assert "no_such_column" in rules
    path = tmp_path / "rules.yaml"
    path.write_text(rules, encoding="utf-8")
    error = await fail_with("DELETE FROM users WHERE id = 1", policy=load_policy(path))
    assert error.reason_code == "policy_columns"


async def test_a_table_without_a_primary_key_fails() -> None:
    scratch = f"scratch_nopk_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(company_url("postgres"))
    try:
        await admin.execute(f"CREATE DATABASE {scratch} TEMPLATE company_template")
        scratch_conn = await asyncpg.connect(company_url(scratch))
        try:
            await scratch_conn.execute("CREATE TABLE no_key (a int)")
        finally:
            await scratch_conn.close()
        client = FakeNeonClient(
            admin_url=COMPANY_ADMIN_URL,
            writer_url=COMPANY_WRITER_URL,
            source_db=scratch,
            clock=CLOCK,
        )
        runner = shadow_runner({"local": client})
        with pytest.raises(ShadowFailedError) as raised:
            await runner.run(
                action_id=uuid.uuid4(),
                profile="local",
                classification=classify("DELETE FROM users WHERE id = 1", now=CLOCK.now()),
            )
        assert raised.value.reason_code == "table_without_primary_key"
        assert await leftover_shadow_databases() == []
    finally:
        await admin.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
        await admin.close()


async def test_a_missing_client_fails_closed() -> None:
    runner = shadow_runner({})
    with pytest.raises(ShadowFailedError) as raised:
        await runner.run(
            action_id=uuid.uuid4(),
            profile="local",
            classification=classify("DELETE FROM users WHERE id = 1", now=CLOCK.now()),
        )
    assert raised.value.reason_code == "neon_not_configured"


class FailingDelete(FakeNeonClient):
    """A client whose delete always fails; the test removes the copy itself afterwards."""

    left: list[ShadowTarget]

    async def delete_shadow(self, target: ShadowTarget) -> None:
        self.left.append(target)
        raise NeonError("neon_500")


async def test_a_failing_delete_keeps_the_first_error(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    base = fake_neon_client(CLOCK)
    client = FailingDelete(admin_url=base._admin_url, writer_url=base._writer_url, clock=CLOCK)
    client.left = []
    runner = shadow_runner({"local": client})
    try:
        with pytest.raises(ShadowFailedError) as raised:
            await runner.run(
                action_id=uuid.uuid4(),
                profile="local",
                classification=classify(
                    "UPDATE users SET plan = 'nope' WHERE id = 1", now=CLOCK.now()
                ),
            )
        assert raised.value.reason_code == "statement_error_23514"
        assert "shadow_delete_failed" in capsys.readouterr().out
    finally:
        for target in client.left:
            await FakeNeonClient.delete_shadow(client, target)
    assert await leftover_shadow_databases() == []


async def test_a_failed_delete_after_success_does_not_hide_the_result() -> None:
    base = fake_neon_client(CLOCK)
    client = FailingDelete(admin_url=base._admin_url, writer_url=base._writer_url, clock=CLOCK)
    client.left = []
    runner = shadow_runner({"local": client})
    try:
        result = await runner.run(
            action_id=uuid.uuid4(),
            profile="local",
            classification=classify("DELETE FROM feature_flags WHERE id = 1", now=CLOCK.now()),
        )
        assert result.diff.tables["feature_flags"].deleted == 1
    finally:
        for target in client.left:
            await FakeNeonClient.delete_shadow(client, target)
