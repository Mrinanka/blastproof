"""POST /v1/actions/{id}/approve and /reject on the real graph (D-053): the done-when of the
pull-forward, who may decide, what a loser sees, and what never reaches a log.

Async tests on `httpx.ASGITransport` with the app's own lifespan, and the rig's runner installed on
`app.state` (the `test_ready_checkpointer.py` pattern): a sync TestClient would run the rig's
pool on another event loop.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.core.clock import FrozenClock
from app.db.actions import status_view
from app.main import create_app
from app.workflow.runner import WorkflowRunner
from tests.action_helpers import token_for
from tests.conftest import TEST_JWT_SECRET, TestUser, asyncpg_url
from tests.workflow_helpers import (
    WorkflowEnv,
    action_row,
    event_types,
    paused_action,
    paused_rig,
    runner_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

REASON = "Not this table, ask the owner first."
NOT_FOUND = {"error": {"code": "not_found", "message": "No action was found."}}


class Api:
    def __init__(self, client: httpx.AsyncClient, env: WorkflowEnv) -> None:
        self.client = client
        self.env = env
        self.other = env.db.make_user()
        self.admin = env.db.make_user(role="admin")

    async def post(
        self, user: TestUser | None, action_id: object, verb: str, body: object = None
    ) -> httpx.Response:
        headers = token_for(user) if user is not None else {}
        return await self.client.post(f"/v1/actions/{action_id}/{verb}", headers=headers, json=body)

    async def approve(self, user: TestUser | None, action_id: object) -> httpx.Response:
        return await self.post(user, action_id, "approve")

    async def reject(
        self, user: TestUser | None, action_id: object, reason: str = REASON
    ) -> httpx.Response:
        return await self.post(user, action_id, "reject", {"reason": reason})

    async def detail(self, user: TestUser, action_id: object) -> dict[str, Any]:
        response = await self.client.get(f"/v1/actions/{action_id}", headers=token_for(user))
        assert response.status_code == 200, response.text
        return response.json()


@asynccontextmanager
async def api_for(env: WorkflowEnv, runner: WorkflowRunner | None) -> AsyncIterator[Api]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=asyncpg_url(env.db.app_url),
        jwt_secret=TEST_JWT_SECRET,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.runner = runner
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Api(client, env)


def insert_with_status(env: WorkflowEnv, status: str) -> uuid.UUID:
    [(action_id,)] = env.db.execute(
        "INSERT INTO actions (user_id, agent_id, system_id, tool, status, tables)"
        " VALUES (%s, %s, %s, 'query_database', %s, '{}') RETURNING id",
        (env.user.id, env.agent_id, env.system_id, status),
    )
    return action_id


# --- the done-when ------------------------------------------------------------------------------


async def test_the_owner_approves_the_paused_replit_action(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            login = await api.client.post(
                "/v1/auth/login",
                json={"email": wf_env.user.email, "password": wf_env.user.password},
            )
            assert login.status_code == 200
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
            listed = await api.client.get("/v1/actions?status=awaiting_review", headers=auth)
            assert str(action_id) in [item["id"] for item in listed.json()["items"]]
            shown = await api.client.get(f"/v1/actions/{action_id}", headers=auth)
            assert shown.json()["can_decide"] is True

            response = await api.client.post(f"/v1/actions/{action_id}/approve", headers=auth)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "approved" and body["id"] == str(action_id)
            assert body["decided_by"]["id"] == str(wf_env.user.id)
            assert body["reviewer_note"] is None

            await runner.join()
            detail = await api.detail(wf_env.user, action_id)
        assert detail["status"] == "committed" and detail["can_decide"] is False
        approved = [e for e in detail["timeline"] if e["event_type"] == "approved"]
        assert [e["data"]["display_name"] for e in approved] == [body["decided_by"]["display_name"]]
        assert body["decided_at"] == detail["decided_at"]
        assert r.committer.committed == [action_id]


async def test_the_owner_rejects_with_a_reason(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            response = await api.reject(wf_env.user, action_id)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "rejected"
            assert response.json()["reviewer_note"] == REASON
            await runner.join()
            detail = await api.detail(wf_env.user, action_id)
        assert detail["status"] == "rejected" and detail["reviewer_note"] == REASON
        async with r.deps.sessionmaker() as session:
            view = await status_view(session, action_id, wf_env.user.id, is_admin=False)
        assert view is not None and view.reviewer_note == REASON  # what the agent will be sent
        assert r.committer.committed == []
        assert await r.next_nodes(action_id) == ()


# --- concurrency --------------------------------------------------------------------------------


async def test_two_approves_at_once_give_one_200_and_one_409(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            responses = await asyncio.gather(
                api.approve(wf_env.user, action_id), api.approve(wf_env.user, action_id)
            )
            await runner.join()
        winner = next(x for x in responses if x.status_code == 200)
        loser = next(x for x in responses if x.status_code == 409)
        name = winner.json()["decided_by"]["display_name"]
        assert loser.json()["error"] == {
            "code": "already_decided",
            "message": f"Already approved by {name}.",
        }
        assert r.committer.committed == [action_id]


async def test_approve_against_reject_has_one_winner(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            approve, reject = await asyncio.gather(
                api.approve(wf_env.user, action_id), api.reject(wf_env.user, action_id)
            )
            await runner.join()
        assert sorted([approve.status_code, reject.status_code]) == [200, 409]
        assert r.committer.committed == ([action_id] if approve.status_code == 200 else [])


# --- who ----------------------------------------------------------------------------------------


async def test_another_member_gets_the_same_404_as_an_unknown_id(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            for target in (action_id, uuid.uuid4(), "not-a-uuid"):
                for response in (
                    await api.approve(api.other, target),
                    await api.reject(api.other, target),
                ):
                    assert (response.status_code, response.json()) == (404, NOT_FOUND)
            await runner.join()
        row = action_row(wf_env, action_id)
        assert row["status"] == "awaiting_review" and row["decided_by"] is None
        assert r.committer.committed == []


async def test_an_admin_can_decide_someone_elses_action(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            response = await api.approve(api.admin, action_id)
            await runner.join()
        assert response.status_code == 200
        assert response.json()["decided_by"]["id"] == str(api.admin.id)
        assert action_row(wf_env, action_id)["decided_by"] == api.admin.id


async def test_anonymous_bad_token_and_deactivated_user_get_401(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        gone = wf_env.db.make_user()
        async with api_for(wf_env, runner) as api:
            wf_env.db.execute("UPDATE users SET is_active = false WHERE id = %s", (gone.id,))
            assert (await api.approve(None, action_id)).status_code == 401
            assert (await api.reject(None, action_id)).status_code == 401
            bad = await api.client.post(
                f"/v1/actions/{action_id}/approve", headers={"Authorization": "Bearer nope"}
            )
            assert bad.status_code == 401
            assert (await api.approve(gone, action_id)).status_code == 401
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


# --- state --------------------------------------------------------------------------------------


async def test_approving_a_rejected_action_is_a_409_naming_the_decider(
    wf_env: WorkflowEnv,
) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            first = await api.reject(api.admin, action_id)
            second = await api.approve(wf_env.user, action_id)
            await runner.join()
        name = first.json()["decided_by"]["display_name"]
        assert second.status_code == 409
        assert second.json()["error"]["message"] == f"Already rejected by {name}."
        assert r.committer.committed == []


async def test_an_overdue_review_is_a_409_and_expires(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        late = FrozenClock(action_row(wf_env, action_id)["expires_at"] + timedelta(seconds=1))
        runner = runner_for(r, clock=late)
        async with api_for(wf_env, runner) as api:
            response = await api.approve(wf_env.user, action_id)
            await runner.join()
        assert response.status_code == 409
        assert response.json()["error"]["message"] == (
            "Nobody decided within the review window; the action expired."
        )
        row = action_row(wf_env, action_id)
        assert row["status"] == "expired" and row["decided_by"] is None
        assert event_types(wf_env, action_id).count("expired") == 1
        assert r.committer.committed == []


async def test_an_action_that_is_not_in_review_is_a_409(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        ids = [insert_with_status(wf_env, status) for status in ("executed", "blocked")]
        async with api_for(wf_env, runner) as api:
            for action_id in ids:
                for response in (
                    await api.approve(wf_env.user, action_id),
                    await api.reject(wf_env.user, action_id),
                ):
                    assert response.status_code == 409
                    assert response.json()["error"]["message"] == (
                        "This action is not waiting for review."
                    )


# --- the runner ---------------------------------------------------------------------------------


async def test_a_recovering_or_draining_runner_is_a_503_and_changes_nothing(
    wf_env: WorkflowEnv,
) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        recovering = runner_for(r, recovered=False)
        draining = runner_for(r)
        await draining.drain()
        for runner in (recovering, draining):
            async with api_for(wf_env, runner) as api:
                for response in (
                    await api.approve(wf_env.user, action_id),
                    await api.reject(wf_env.user, action_id),
                    await api.approve(api.other, uuid.uuid4()),
                ):
                    assert response.status_code == 503
                    assert response.json()["error"]["code"] == "unavailable"
                    assert response.headers["retry-after"] == "10"
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_no_runner_is_a_503(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        action_id = await paused_action(r)
        async with api_for(wf_env, None) as api:
            response = await api.approve(wf_env.user, action_id)
        assert response.status_code == 503
        assert "not configured" in response.json()["error"]["message"]
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


# --- the body -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [None, {}, {"reason": "x" * 9}, {"reason": "x" * 9 + "      "}, {"reason": "y" * 501}]
)
async def test_a_bad_reject_body_is_a_422_that_does_not_echo_it(
    wf_env: WorkflowEnv, body: dict[str, str] | None
) -> None:
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            response = await api.post(wf_env.user, action_id, "reject", body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        if body and body.get("reason", "").strip():
            assert body["reason"].strip() not in response.text
        assert action_row(wf_env, action_id)["status"] == "awaiting_review"


async def test_a_malformed_id_is_a_404_even_with_a_bad_body(wf_env: WorkflowEnv) -> None:
    async with paused_rig(wf_env) as r:
        async with api_for(wf_env, runner_for(r)) as api:
            response = await api.post(wf_env.user, "not-a-uuid", "reject", {"reason": "short"})
        assert (response.status_code, response.json()) == (404, NOT_FOUND)


# --- logs ---------------------------------------------------------------------------------------


async def test_the_reason_is_never_logged(
    wf_env: WorkflowEnv, caplog: pytest.LogCaptureFixture
) -> None:
    planted = "planted.reviewer@example.com"
    async with paused_rig(wf_env) as r:
        runner = runner_for(r)
        action_id = await paused_action(r)
        async with api_for(wf_env, runner) as api:
            with caplog.at_level("DEBUG"):
                response = await api.reject(wf_env.user, action_id, f"Ask {planted} first, please")
                await runner.join()
        assert response.status_code == 200
    # caplog, not structlog's capture_logs: after configure_logging loggers are cached.
    assert caplog.records, "nothing was captured, so this test would prove nothing"
    text = " ".join(f"{rec.getMessage()} {rec.__dict__}" for rec in caplog.records)
    assert "decision_claimed" in text, "the route's own log line was not captured"
    assert planted not in text and "first, please" not in text


# --- OpenAPI ------------------------------------------------------------------------------------


def test_openapi_describes_both_routes(auth_settings: Settings) -> None:
    spec = create_app(auth_settings).openapi()
    for verb in ("approve", "reject"):
        operation = spec["paths"][f"/v1/actions/{{action_id}}/{verb}"]["post"]
        for code in ("401", "404", "409", "503"):
            ref = operation["responses"][code]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse"), (verb, code)
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ok.endswith("/DecisionResponse")
    reason = spec["components"]["schemas"]["RejectRequest"]["properties"]["reason"]
    assert reason["minLength"] == 10
