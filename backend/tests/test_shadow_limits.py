"""BE-57: at most MAX_CONCURRENT_SHADOWS previews run at once; the next waits, then gets busy.

The measuring is replaced by a sleep, so this tests the slots and nothing else.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.services.classifier import classify
from app.services.neon import ShadowTarget
from app.services.shadow import Diff, ShadowBusyError, ShadowRunner
from tests.conftest import COMPANY_WRITER_URL, shadow_runner

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("company_db_seeded")]

CLOCK = FrozenClock(datetime(2026, 10, 1, tzinfo=UTC))
WRITE = classify("DELETE FROM users WHERE id = 1", now=CLOCK.now())


class SlowClient:
    """A copy that is `company_main` itself (nothing is ever written: _measure is replaced)."""

    def __init__(self) -> None:
        self.created = 0
        self.deleted = 0

    async def create_shadow(self, action_id: uuid.UUID) -> ShadowTarget:
        self.created += 1
        return ShadowTarget(
            branch_id="stub",
            connect_kwargs={
                "dsn": COMPANY_WRITER_URL,
            },
        )

    async def delete_shadow(self, target: ShadowTarget) -> None:
        self.deleted += 1

    async def sweep(self, older_than: Any) -> int:
        return 0

    async def ping(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def slow_runner(
    monkeypatch: pytest.MonkeyPatch, hold: float, **overrides: Any
) -> tuple[ShadowRunner, dict]:
    seen = {"now": 0, "peak": 0}

    async def measure(self: ShadowRunner, conn: Any, classification: Any) -> Diff:
        seen["now"] += 1
        seen["peak"] = max(seen["peak"], seen["now"])
        try:
            await asyncio.sleep(hold)
        finally:
            seen["now"] -= 1
        return Diff(tables={})

    monkeypatch.setattr(ShadowRunner, "_measure", measure)
    client = SlowClient()
    seen["client"] = client
    return shadow_runner({"local": client}, max_concurrent=2, **overrides), seen


async def run(runner: ShadowRunner) -> Any:
    return await runner.run(action_id=uuid.uuid4(), profile="local", classification=WRITE)


async def test_at_most_two_run_and_the_third_gets_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, seen = slow_runner(monkeypatch, hold=0.6, slot_wait_seconds=0.2)
    results = await asyncio.gather(run(runner), run(runner), run(runner), return_exceptions=True)
    assert seen["peak"] == 2
    assert sum(isinstance(r, ShadowBusyError) for r in results) == 1
    client = seen["client"]
    assert client.created == 2 and client.deleted == 2


async def test_a_slot_freed_in_time_lets_the_third_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, seen = slow_runner(monkeypatch, hold=0.3, slot_wait_seconds=5)
    results = await asyncio.gather(run(runner), run(runner), run(runner))
    assert seen["peak"] == 2 and len(results) == 3
    assert seen["client"].created == 3


async def test_a_busy_caller_is_told_to_try_again_later(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _ = slow_runner(monkeypatch, hold=0.5, slot_wait_seconds=0.05)
    holders = [asyncio.create_task(run(runner)) for _ in range(2)]
    await asyncio.sleep(0.1)
    with pytest.raises(ShadowBusyError) as raised:
        await run(runner)
    assert raised.value.reason_code == "shadow_busy"
    await asyncio.gather(*holders)
