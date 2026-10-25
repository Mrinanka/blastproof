"""The clock the classifier pins SQL time functions to (D-017).

The app uses `SystemClock`; tests and evals use `FrozenClock`, so a statement such as
`now() - interval '6 months'` always means the same instant.
"""

from datetime import UTC, datetime
from typing import Protocol


def require_aware_utc(value: datetime) -> datetime:
    """`value` in UTC; a naive datetime is refused, because it has no instant."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock times must be timezone-aware")
    return value.astimezone(UTC)


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware, in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        self._at = require_aware_utc(at)

    def now(self) -> datetime:
        return self._at
