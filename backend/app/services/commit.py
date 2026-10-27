"""Committing an approved write to the company database.

The graph calls a `Committer` after approval. `CompanyCommitter` (`committer.py`) is the real one:
it applies the approved `pinned_sql` only if the company database still matches the preview.
`StubCommitter` applies nothing and says so; tests and `seed-demo-actions` use it.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class CommitOutcome:
    # Rows the write changed, as the real commit counts them; None for the stub.
    rows: int | None
    stub: bool
    # The company database had this action already (a crash after COMMIT, or a second call).
    already_applied: bool = False


class CommitDriftError(Exception):
    """The company database no longer matches the preview. The commit rolled back and changed
    nothing. `counts` are numbers only (see `commit_sql.Drift`)."""

    def __init__(self, reason: str, counts: dict[str, int]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.counts = counts


class CommitFailedError(Exception):
    """The commit did not happen and cannot be retried as it is. `reason_code` goes to the agent."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class CommitOutcomeUnknownError(Exception):
    """The commit may or may not have reached the company database and it could not be told. The
    action stays `committing` for the sweep to settle: it must never be marked failed."""


class Committer(Protocol):
    async def commit(self, action_id: uuid.UUID) -> CommitOutcome: ...


class StubCommitter:
    """Applies nothing. Its `committed` event carries `stub: true`, so seeded or test data made
    with it is recognisable."""

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        logger.info("commit_stub", action_id=str(action_id))
        return CommitOutcome(rows=None, stub=True)
