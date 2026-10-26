"""Committing an approved write to the company database.

Step 14 only needs the seam: the graph calls a `Committer` after approval. `StubCommitter` applies
nothing and says so; step 16 replaces it with the real commit (drift check, row-count check,
`applied_actions` row). The graph must not ship to agents before step 16 (PROGRESS.md).
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


class Committer(Protocol):
    async def commit(self, action_id: uuid.UUID) -> CommitOutcome: ...


class StubCommitter:
    """Applies nothing. Its `committed` event carries `stub: true`, so seeded or test data made
    with it is recognisable."""

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        logger.info("commit_stub", action_id=str(action_id))
        return CommitOutcome(rows=None, stub=True)
