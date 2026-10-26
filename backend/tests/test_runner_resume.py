"""`WorkflowRunner.resume`: the claim, who may make it, and what a lost or stale claim leaves."""

import uuid

import pytest

from app.workflow.runner import (
    ActionNotFoundError,
    AlreadyDecidedError,
    WorkflowUnavailableError,
)
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    decider_for,
    events,
    new_action,
    paused_action,
    paused_rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


async def test_the_claim_stores_who_when_and_why(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        who = decider_for(wf_env.user, name="Dana Reviewer")
        view = await runner.resume(action_id, approved=True, note="looks fine", decider=who)
        await runner.join()
        assert (
            view.status == "approved"
            and view.decided_by_name == decider_for(wf_env.user).display_name
        )
        row = action_row(wf_env, action_id)
        assert row["decided_by"] == wf_env.user.id and row["decided_at"] is not None
        [data] = [d for kind, d in events(wf_env, action_id) if kind == "approved"]
        assert data == {"user_id": str(wf_env.user.id), "display_name": "Dana Reviewer"}
        [(note,)] = wf_env.db.execute(
            "SELECT reviewer_note FROM actions WHERE id = %s", (action_id,)
        )
        assert note == "looks fine"


async def test_another_members_action_is_not_found_and_unchanged(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        stranger = wf_env.db.make_user()
        with pytest.raises(ActionNotFoundError):
            await runner.resume(action_id, approved=True, note=None, decider=decider_for(stranger))
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"
        assert r.committer.committed == []


async def test_an_admin_can_claim_and_an_unknown_id_is_not_found(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        admin = wf_env.db.make_user(role="admin")
        with pytest.raises(ActionNotFoundError):
            await runner.resume(
                uuid.uuid4(), approved=True, note=None, decider=decider_for(admin, is_admin=True)
            )
        await runner.resume(
            action_id, approved=False, note="no", decider=decider_for(admin, is_admin=True)
        )
        await runner.join()
        row = action_row(wf_env, action_id)
        assert row["status"] == "rejected" and row["decided_by"] == admin.id


async def test_an_already_decided_action_names_the_decider(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        await runner.resume(
            action_id, approved=False, note="no", decider=decider_for(wf_env.user, name="Dana R")
        )
        with pytest.raises(AlreadyDecidedError) as raised:
            await runner.resume(
                action_id, approved=True, note=None, decider=decider_for(wf_env.user)
            )
        await runner.join()
        assert (
            raised.value.status == "rejected"
            and raised.value.decided_by_name == decider_for(wf_env.user).display_name
        )


def awaiting_without_thread(env: WorkflowEnv) -> uuid.UUID:
    action_id = new_action(env, "DELETE FROM orders WHERE id > 0")
    env.db.execute(
        "UPDATE actions SET status = 'awaiting_review', expires_at = now() + interval '1 day'"
        " WHERE id = %s",
        (action_id,),
    )
    return action_id


async def test_an_approval_with_no_thread_fails_with_resume_mismatch(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = awaiting_without_thread(wf_env)
        await runner.resume(action_id, approved=True, note=None, decider=decider_for(wf_env.user))
        await runner.join()
        row = action_row(wf_env, action_id)
        assert (row["status"], row["failure_reason"]) == ("failed", "resume_mismatch")
        assert r.committer.committed == []


async def test_a_rejection_with_no_thread_stays_rejected(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = awaiting_without_thread(wf_env)
        await runner.resume(action_id, approved=False, note="no", decider=decider_for(wf_env.user))
        await runner.join()
        assert action_row(wf_env, action_id)["status"] == "rejected"


async def test_resume_while_recovering_makes_no_claim(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        runner = runner_for(r, recovered=False)
        with pytest.raises(WorkflowUnavailableError) as raised:
            await runner.resume(
                action_id, approved=True, note=None, decider=decider_for(wf_env.user)
            )
        assert raised.value.reason == "recovering"
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_the_claim_statement_itself_refuses_another_members_action(
    wf_env: WorkflowEnv,
) -> None:
    """Visibility is part of the UPDATE, so no caller can skip it by calling the claim directly."""
    from datetime import UTC, datetime

    from app.db.actions import claim_decision

    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        stranger = wf_env.db.make_user()
        async with r.deps.sessionmaker.begin() as session:
            won = await claim_decision(
                session,
                action_id,
                user_id=stranger.id,
                is_admin=False,
                approved=True,
                note=None,
                now=datetime.now(UTC),
            )
        assert not won and action_row(wf_env, action_id)["status"] == "awaiting_review"
