"""Step 16: how the runner settles an action left in `committing` (BE-85), with a fake committer."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import FrozenClock
from app.services.commit import CommitOutcomeUnknownError
from tests.workflow_helpers import (
    RecordingCommitter,
    WorkflowEnv,
    action_row,
    claim,
    paused_action,
    paused_rig,
    runner_for,
)

pytestmark = pytest.mark.integration


async def committing_action(r):  # type: ignore[no-untyped-def]
    """An approved write whose commit could not tell whether it happened: `committing`, with the
    thread paused before `commit`."""
    action_id = await paused_action(r)
    assert claim(r.env, action_id, approved=True) == 1
    with pytest.raises(CommitOutcomeUnknownError):
        await r.resume(action_id, approved=True)
    assert action_row(r.env, action_id)["status"] == "committing"
    assert await r.next_nodes(action_id) == ("commit",)
    return action_id


async def test_recovery_continues_a_committing_action_paused_before_the_commit(
    fresh_wf_env: WorkflowEnv,
) -> None:
    committer = RecordingCommitter(raises=CommitOutcomeUnknownError())
    async with paused_rig(fresh_wf_env, committer=committer) as r:
        action_id = await committing_action(r)
        committer.raises = None
        runner = runner_for(r, recovered=False)
        report = await runner.recover()
        await runner.join()

    assert report is not None and report.reconciled == 1 and report.untouched_committing == 0
    assert action_row(fresh_wf_env, action_id)["status"] == "committed"
    assert committer.committed == [action_id]


async def test_the_sweep_reconciles_an_old_commit_but_skips_one_running_in_this_process(
    fresh_wf_env: WorkflowEnv,
) -> None:
    committer = RecordingCommitter(raises=CommitOutcomeUnknownError())
    async with paused_rig(fresh_wf_env, committer=committer) as r:
        action_id = await committing_action(r)
        committer.raises = None
        later = FrozenClock(datetime.now(UTC) + timedelta(minutes=6))

        runner = runner_for(r, clock=later)
        runner._running.add(action_id)  # a node of this process is still working on it
        skipped = await runner.sweep()
        assert skipped is not None and skipped.reconciled == 0
        assert action_row(fresh_wf_env, action_id)["status"] == "committing"

        runner._running.discard(action_id)
        report = await runner.sweep()
        await runner.join()

    assert report is not None and report.reconciled == 1 and report.failed_stuck == 0
    assert action_row(fresh_wf_env, action_id)["status"] == "committed"
    assert committer.committed == [action_id]


async def test_the_sweep_never_fails_a_committing_action(fresh_wf_env: WorkflowEnv) -> None:
    committer = RecordingCommitter(raises=CommitOutcomeUnknownError())
    async with paused_rig(fresh_wf_env, committer=committer) as r:
        action_id = await committing_action(r)
        # Still unknown on the next try: it stays `committing`, it is never marked failed.
        runner = runner_for(r, clock=FrozenClock(datetime.now(UTC) + timedelta(hours=3)))
        await runner.sweep()
        await runner.join()

    row = action_row(fresh_wf_env, action_id)
    assert row["status"] == "committing" and row["failure_reason"] is None


async def test_a_failure_writing_committed_after_the_commit_never_marks_the_action_failed(
    fresh_wf_env: WorkflowEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The company database has the write; failing the action would say it does not."""
    from app.db.enums import ActionStatus
    from app.workflow import graph

    real = graph.set_status

    async def fail_on_committed(session, action_id, status, **kwargs):  # type: ignore[no-untyped-def]
        if status == ActionStatus.COMMITTED:
            raise RuntimeError("Blastproof's database went away")
        return await real(session, action_id, status, **kwargs)

    committer = RecordingCommitter()
    async with paused_rig(fresh_wf_env, committer=committer) as r:
        action_id = await paused_action(r)
        assert claim(r.env, action_id, approved=True) == 1
        monkeypatch.setattr(graph, "set_status", fail_on_committed)
        with pytest.raises(CommitOutcomeUnknownError):
            await r.resume(action_id, approved=True)
        monkeypatch.undo()

        row = action_row(fresh_wf_env, action_id)
        assert row["status"] == "committing" and row["failure_reason"] is None
        assert await r.next_nodes(action_id) == ("commit",)
        # The rerun settles it.
        runner = runner_for(r, recovered=False)
        await runner.recover()
        await runner.join()

    assert action_row(fresh_wf_env, action_id)["status"] == "committed"
