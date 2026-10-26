"""BE-77, BE-78: pausing at `human_review` and resuming it. The status after a resume is
`approved`, never `awaiting_review`; a restarted process resumes a paused thread; a resume whose
approval no longer stands changes nothing and never commits.

Step 15's SQL claim is done by hand (`claim`): the graph only reads its outcome.
"""

import uuid
from typing import Any

import pytest

from app.services.commit import CommitOutcome
from app.workflow.graph import NotAwaitingReviewError
from tests.impact_helpers import REPLIT_INTENT, REPLIT_SQL, replit_diff
from tests.workflow_helpers import (
    RecordingCommitter,
    WorkflowEnv,
    action_row,
    claim,
    events,
    new_action,
    rig,
    status_history,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


class ProbeCommitter(RecordingCommitter):
    """Records the action's status at the moment the commit runs."""

    def __init__(self, env: WorkflowEnv) -> None:
        super().__init__()
        self.env = env
        self.status_seen: list[str] = []

    async def commit(self, action_id: uuid.UUID) -> CommitOutcome:
        self.status_seen.append(action_row(self.env, action_id)["status"])
        return await super().commit(action_id)


async def paused(r: Any, env: WorkflowEnv) -> uuid.UUID:
    action_id = new_action(env, REPLIT_SQL, intent=REPLIT_INTENT)
    await r.run(action_id)
    assert action_row(env, action_id)["status"] == "awaiting_review"
    return action_id


async def test_status_after_resume_is_approved(wf_env: WorkflowEnv) -> None:
    """DW-B14: the status never goes back to `awaiting_review` (the reason D-025 split the node)."""
    committer = ProbeCommitter(wf_env)
    async with rig(wf_env, diff=replit_diff(), committer=committer) as r:
        action_id = await paused(r, wf_env)
        claim(wf_env, action_id, approved=True)
        await r.resume(action_id, approved=True)

        # The commit ran on an action that was already committing (so after `approved`) ...
        assert committer.status_seen == ["committing"]
        history = status_history(wf_env, action_id)
        assert history[history.index("awaiting_review") :] == [
            "awaiting_review",
            "approved",
            "committing",
            "committed",
        ]
        # ... and exactly one `awaiting_review` event was ever written (BE-77).
        assert history.count("awaiting_review") == 1
        assert history.count("approved") == 1


async def test_a_second_process_resumes_a_paused_thread(wf_env: WorkflowEnv) -> None:
    """A restart: a new pool, saver and graph object pick the thread up from Postgres."""
    async with rig(wf_env, diff=replit_diff()) as first:
        action_id = await paused(first, wf_env)

    committer = RecordingCommitter()
    async with rig(wf_env, committer=committer) as second:  # no diff: it must not preview again
        assert await second.next_nodes(action_id) == ("human_review",)
        claim(wf_env, action_id, approved=True)
        await second.resume(action_id, approved=True)

        assert second.shadow.calls == []
        assert action_row(wf_env, action_id)["status"] == "committed"
        assert committer.committed == [action_id]


async def test_resuming_a_thread_that_is_not_paused_is_refused(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = await paused(r, wf_env)
        claim(wf_env, action_id, approved=True)
        await r.resume(action_id, approved=True)
        # A second resume of the finished thread (D-177): refused before LangGraph is called.
        with pytest.raises(NotAwaitingReviewError):
            await r.resume(action_id, approved=True)
        with pytest.raises(NotAwaitingReviewError):
            await r.resume(uuid.uuid4(), approved=True)  # an unknown thread
        assert r.committer.committed == [action_id]  # committed once


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE actions SET status = 'expired' WHERE id = %s",  # the sweeper got there first
        "UPDATE actions SET status = 'rejected' WHERE id = %s",  # rejected, value says approve
        None,  # never claimed: the row still says awaiting_review
    ],
    ids=["expired", "rejected", "unclaimed"],
)
async def test_a_resume_whose_approval_no_longer_stands_changes_nothing(
    wf_env: WorkflowEnv, change: str | None
) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = await paused(r, wf_env)
        before = action_row(wf_env, action_id)["status"]
        if change is not None:
            wf_env.db.execute(change, (action_id,))
            before = action_row(wf_env, action_id)["status"]
        await r.resume(action_id, approved=True)  # the resume value says approve anyway

        assert action_row(wf_env, action_id)["status"] == before
        assert r.committer.committed == []
        mismatch = [data for kind, data in events(wf_env, action_id) if kind == "fail_closed"]
        assert mismatch == [{"reason": "resume_mismatch", "status": before}]
        assert "committing" not in status_history(wf_env, action_id)


async def test_a_rejected_resume_never_commits(wf_env: WorkflowEnv) -> None:
    async with rig(wf_env, diff=replit_diff()) as r:
        action_id = await paused(r, wf_env)
        claim(wf_env, action_id, approved=False)
        await r.resume(action_id, approved=False)
        assert action_row(wf_env, action_id)["status"] == "rejected"
        assert r.committer.committed == []
        assert [k for k, _ in events(wf_env, action_id)].count("fail_closed") == 0
