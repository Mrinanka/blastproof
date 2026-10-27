"""The pure parts of the stats: the UTC day and the zero-filled counts. No Docker."""

from datetime import UTC, datetime, timedelta, timezone

from app.db.enums import ActionStatus, Decision
from app.services.stats import day_window, fill_decision, fill_status


def test_the_day_is_the_utc_day() -> None:
    start, end = day_window(datetime(2026, 10, 27, 12, 30, tzinfo=UTC))
    assert start == datetime(2026, 10, 27, tzinfo=UTC)
    assert end == datetime(2026, 10, 28, tzinfo=UTC)


def test_the_last_millisecond_and_midnight_fall_on_different_days() -> None:
    late = datetime(2026, 10, 27, 23, 59, 59, 999000, tzinfo=UTC)
    midnight = datetime(2026, 10, 28, tzinfo=UTC)
    assert day_window(late)[0] == datetime(2026, 10, 27, tzinfo=UTC)
    assert day_window(midnight)[0] == midnight
    assert day_window(late)[1] == midnight  # end is exclusive


def test_another_time_zone_is_converted_first() -> None:
    plus_two = timezone(timedelta(hours=2))
    start, _ = day_window(datetime(2026, 10, 28, 1, 0, tzinfo=plus_two))  # 23:00 UTC the day before
    assert start == datetime(2026, 10, 27, tzinfo=UTC)


def test_every_status_and_decision_is_present() -> None:
    statuses = fill_status({"committed": 3})
    assert set(statuses) == set(ActionStatus)
    assert statuses[ActionStatus.COMMITTED] == 3
    assert sum(statuses.values()) == 3
    decisions = fill_decision({})
    assert set(decisions) == set(Decision)
    assert not any(decisions.values())
