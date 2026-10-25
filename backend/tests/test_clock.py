"""The injected clock (D-017): aware UTC, and a frozen one for tests and evals."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.clock import FrozenClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


def test_frozen_clock_is_fixed() -> None:
    clock = FrozenClock(datetime(2026, 10, 1, tzinfo=UTC))
    assert clock.now() == clock.now() == datetime(2026, 10, 1, tzinfo=UTC)


def test_frozen_clock_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 10, 1))


def test_frozen_clock_converts_to_utc() -> None:
    clock = FrozenClock(datetime(2026, 10, 1, 2, tzinfo=timezone(timedelta(hours=2))))
    assert clock.now() == datetime(2026, 10, 1, tzinfo=UTC)
    assert clock.now().utcoffset() == timedelta(0)


def test_frozen_clock_fixture_is_the_anchor_instant(frozen_clock: FrozenClock) -> None:
    assert frozen_clock.now() == datetime(2026, 10, 1, tzinfo=UTC)
