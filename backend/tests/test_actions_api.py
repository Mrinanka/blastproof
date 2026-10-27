"""BE-109, BE-110, ARCH-14, ARCH-15: GET /v1/actions and GET /v1/actions/{id}."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
import structlog
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.actions import ActionDetail, ActionPage, ActionSummary
from tests.action_helpers import (
    DISPLAY_SQL,
    PINNED_SQL,
    RAW_SQL,
    full_action_columns,
    insert_action,
    insert_event,
    make_system,
    token_for,
)
from tests.conftest import TEST_JWT_SECRET, AuthDb, TestAgent, TestUser, asyncpg_url

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

NOT_FOUND = {"error": {"code": "not_found", "message": "No action was found."}}
CONTRACT_KEYS = set(ActionDetail.model_fields)


@dataclass
class World:
    db: AuthDb
    client: TestClient
    owner: TestUser
    owner_agent: TestAgent
    other: TestUser
    other_agent: TestAgent
    admin: TestUser
    system_id: uuid.UUID

    def add(self, who: str = "owner", **columns: object) -> uuid.UUID:
        user, agent = (
            (self.owner, self.owner_agent) if who == "owner" else (self.other, self.other_agent)
        )
        return insert_action(self.db, user, agent.id, self.system_id, **columns)

    def get(self, user: TestUser, path: str):  # type: ignore[no-untyped-def]
        return self.client.get(path, headers=token_for(user))


@pytest.fixture
def world(auth_db: AuthDb, auth_client: TestClient) -> World:
    owner, other = auth_db.make_user(), auth_db.make_user()
    return World(
        auth_db,
        auth_client,
        owner,
        auth_db.make_agent(owner.id),
        other,
        auth_db.make_agent(other.id),
        auth_db.make_user(role="admin"),
        make_system(auth_db),
    )


def ids(response) -> list[str]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# --- the list -----------------------------------------------------------------------------------


def test_list_shows_only_the_callers_actions(world: World) -> None:
    mine, theirs = world.add(), world.add("other")
    assert ids(world.get(world.owner, "/v1/actions")) == [str(mine)]
    assert ids(world.get(world.other, "/v1/actions")) == [str(theirs)]


def test_admin_sees_everyones_actions(world: World) -> None:
    mine, theirs = world.add(), world.add("other")
    response = world.get(world.admin, "/v1/actions?limit=100")
    assert {str(mine), str(theirs)} <= set(ids(response))


def test_member_with_no_actions_gets_an_empty_page(world: World, auth_db: AuthDb) -> None:
    world.add()
    nobody = auth_db.make_user()
    body = world.get(nobody, "/v1/actions").json()
    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_summary_shape_and_masking(world: World) -> None:
    action = world.add(**full_action_columns())
    [item] = world.get(world.owner, "/v1/actions").json()["items"]
    assert set(item) == set(ActionSummary.model_fields)
    assert item["id"] == str(action)
    assert item["headline"] == "Deletes 10 users and 4 orders."
    assert item["intent"] == "Clean up inactive users, ask [email]"
    assert item["agent"] == {"id": str(world.owner_agent.id), "name": "test agent"}
    assert item["decided_by"] is None
    assert item["tables"] == ["users", "orders"]


def test_summary_without_a_report_has_no_headline_and_names_the_decider(world: World) -> None:
    world.add(decided_by=world.admin.id, decided_at=datetime.now(UTC))
    [item] = world.get(world.owner, "/v1/actions").json()["items"]
    assert item["headline"] is None
    assert item["decided_by"]["id"] == str(world.admin.id)
    assert set(item["decided_by"]) == {"id", "display_name"}


def test_filter_by_status_alone_and_repeated(world: World) -> None:
    done = world.add(status="executed")
    waiting = world.add(status="awaiting_review")
    blocked = world.add(status="blocked")
    assert ids(world.get(world.owner, "/v1/actions?status=blocked")) == [str(blocked)]
    both = ids(world.get(world.owner, "/v1/actions?status=blocked&status=awaiting_review"))
    assert set(both) == {str(blocked), str(waiting)}
    assert str(done) not in both


def test_total_counts_only_the_filtered_actions(world: World) -> None:
    for _ in range(3):
        world.add(status="blocked")
    world.add(status="executed")
    body = world.get(world.owner, "/v1/actions?status=blocked&limit=2").json()
    assert body["total"] == 3 and len(body["items"]) == 2


def test_filter_by_agent(world: World, auth_db: AuthDb) -> None:
    second = auth_db.make_agent(world.owner.id)
    first = world.add()
    other = insert_action(auth_db, world.owner, second.id, world.system_id)
    assert ids(world.get(world.owner, f"/v1/actions?agent_id={second.id}")) == [str(other)]
    url = f"/v1/actions?agent_id={world.owner_agent.id}"
    assert ids(world.get(world.owner, url)) == [str(first)]


def test_filter_by_table_matches_array_membership(world: World) -> None:
    users = world.add(tables=["users"])
    orders = world.add(tables=["orders"])
    both = world.add(tables=["users", "orders"])
    assert set(ids(world.get(world.owner, "/v1/actions?table=users"))) == {str(users), str(both)}
    assert set(ids(world.get(world.owner, "/v1/actions?table=orders"))) == {str(orders), str(both)}
    assert ids(world.get(world.owner, "/v1/actions?table=use")) == []


def test_filter_by_time_is_inclusive_from_and_exclusive_to(world: World) -> None:
    edge = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    before = world.add(created_at=edge - timedelta(seconds=1))
    at = world.add(created_at=edge)
    after = world.add(created_at=edge + timedelta(seconds=1))
    stamp = quote(edge.isoformat())
    assert set(ids(world.get(world.owner, f"/v1/actions?from={stamp}"))) == {str(at), str(after)}
    assert set(ids(world.get(world.owner, f"/v1/actions?to={stamp}"))) == {str(before)}
    end = quote((edge + timedelta(seconds=1)).isoformat())
    assert ids(world.get(world.owner, f"/v1/actions?from={stamp}&to={end}")) == [str(at)]


def test_filters_combine(world: World) -> None:
    hit = world.add(status="blocked", tables=["users"])
    world.add(status="blocked", tables=["orders"])
    world.add(status="executed", tables=["users"])
    url = f"/v1/actions?status=blocked&table=users&agent_id={world.owner_agent.id}"
    assert ids(world.get(world.owner, url)) == [str(hit)]


def test_order_newest_and_oldest_with_id_as_the_tiebreak(world: World) -> None:
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    older = world.add(created_at=moment)
    ties = sorted(world.add(created_at=moment + timedelta(hours=1)) for _ in range(2))
    newest = ids(world.get(world.owner, "/v1/actions"))
    assert newest == [str(ties[1]), str(ties[0]), str(older)]
    oldest = ids(world.get(world.owner, "/v1/actions?order=oldest"))
    assert oldest == [str(older), str(ties[0]), str(ties[1])]


def test_paging_keeps_the_total_and_ends_with_a_partial_page(world: World) -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    created = {str(world.add(created_at=start + timedelta(minutes=i))) for i in range(5)}
    seen: list[str] = []
    for offset, expected in ((0, 2), (2, 2), (4, 1)):
        body = world.get(world.owner, f"/v1/actions?limit=2&offset={offset}").json()
        assert body["total"] == 5 and body["limit"] == 2 and body["offset"] == offset
        assert len(body["items"]) == expected
        seen += [item["id"] for item in body["items"]]
    assert set(seen) == created and len(seen) == 5
    past = world.get(world.owner, "/v1/actions?limit=2&offset=50").json()
    assert past == {"items": [], "total": 5, "limit": 2, "offset": 50}


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=abc",
        "offset=-1",
        "from=2026-05-01T12:00:00",
        "to=2026-05-01T12:00:00",
        "from=2026-05-02T00:00:00Z&to=2026-05-01T00:00:00Z",
        "from=2026-05-01T00:00:00Z&to=2026-05-01T00:00:00Z",
        "status=nonsense",
        "agent_id=not-a-uuid",
        "order=sideways",
        f"table={'x' * 64}",
        "table=",
    ],
)
def test_bad_query_is_a_422_in_the_envelope(world: World, query: str) -> None:
    response = world.get(world.owner, f"/v1/actions?{query}")
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == "validation_error"


def test_unknown_query_parameters_are_ignored(world: World) -> None:
    world.add()
    assert len(ids(world.get(world.owner, "/v1/actions?colour=red"))) == 1


# --- the detail ---------------------------------------------------------------------------------


def test_detail_has_the_whole_contract(world: World) -> None:
    previous = world.add(status="blocked")
    action = world.add(**full_action_columns(retry_of=previous, reviewer_note="Looks fine."))
    retry = world.add(retry_of=action)
    insert_event(world.db, action, "received")
    insert_event(world.db, action, "classified", {"statement_type": "DELETE", "nested": {"a": 1}})
    response = world.get(world.owner, f"/v1/actions/{action}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == CONTRACT_KEYS
    assert body["id"] == str(action)
    assert body["status"] == "awaiting_review" and body["decision"] == "review"
    assert body["risk_level"] == "high"
    assert body["intent"] == "Clean up inactive users, ask [email]"
    assert body["sql_display"] == DISPLAY_SQL
    assert body["pinned_values"] == {"now()": "2026-10-01T00:00:00Z"}
    original = body["original_sql_display"]
    assert original is not None and "raw.secret" not in original
    assert body["statement_type"] == "DELETE" and body["tables"] == ["users", "orders"]
    assert body["impact_report"] == {
        "headline": "Deletes 10 users and 4 orders.",
        "key_points": ["2 of the users are on the pro plan."],
        "concerns": ["Paying customers are affected."],
        "source": "llm",
    }
    assert body["table_changes"] == [
        {"table": "users", "deleted": 10, "updated": 0, "inserted": 0, "is_cascade": False},
        {"table": "orders", "deleted": 4, "updated": 0, "inserted": 0, "is_cascade": True},
    ]
    assert body["breakdowns"] == [
        {"table": "users", "column": "plan", "counts": {"free": 8, "pro": 2}}
    ]
    [group] = body["derived_groups"]
    assert (group["table"], group["name"], group["count"]) == ("users", "paying", 2)
    assert group["definition"]  # read from the shipped rules.yaml
    assert len(body["samples"]) == 2 and body["samples"][0]["side"] == "before"
    assert body["judge"] == {
        "provider": "llm",
        "model": "google_genai:test",
        "recommendation": "review",
        "risk_level": "high",
        "confidence": 0.9,
        "reason": "Paying users.",
        "failure": None,
    }
    assert body["matched_rules"] == [
        {"name": "Delete many", "outcome": "review", "reason": "Large delete.", "pass": "post"}
    ]
    [ref] = body["knowledge_used"]
    assert ref["first_line"] == "Users: mail [email]" and ref["match"] == "table"
    assert body["policy_reason"] == "Deletes paying customers."
    assert body["reviewer_note"] == "Looks fine."
    assert body["retry_of"] == str(previous) and body["retried_by"] == str(retry)
    assert [e["event_type"] for e in body["timeline"]] == ["received", "classified"]
    assert body["timeline"][1]["data"] == {"statement_type": "DELETE"}
    assert body["timings"] == {"preview_ms": 12000, "total_ms": 15000}
    assert body["llm_cost_usd"] == pytest.approx(0.0022)
    assert body["owner"]["id"] == str(world.owner.id)
    assert set(body["owner"]) == {"id", "display_name"}
    assert body["decided_by"] is None and body["can_decide"] is True
    ActionDetail.model_validate(body)


def test_detail_of_a_read_action_has_nulls_and_empty_lists(world: World) -> None:
    action = world.add(tool="list_tables")
    body = world.get(world.owner, f"/v1/actions/{action}").json()
    assert set(body) == CONTRACT_KEYS
    assert body["intent"] is None and body["sql_display"] is None
    assert body["statement_type"] is None and body["impact_report"] is None
    assert body["judge"] is None and body["pinned_values"] == {}
    assert body["original_sql_display"] is None
    for key in (
        "table_changes",
        "breakdowns",
        "derived_groups",
        "samples",
        "matched_rules",
        "knowledge_used",
        "timeline",
    ):
        assert body[key] == [], key
    assert body["can_decide"] is False and body["llm_cost_usd"] is None


def test_unknown_malformed_and_foreign_ids_give_the_same_404(world: World) -> None:
    theirs = world.add("other")
    for path in (f"/v1/actions/{theirs}", f"/v1/actions/{uuid.uuid4()}", "/v1/actions/not-a-uuid"):
        response = world.get(world.owner, path)
        assert response.status_code == 404, path
        assert response.json() == NOT_FOUND, path


def test_admin_opens_another_users_action_and_may_decide(world: World) -> None:
    action = world.add(**full_action_columns())
    response = world.get(world.admin, f"/v1/actions/{action}")
    assert response.status_code == 200
    assert response.json()["can_decide"] is True
    assert response.json()["owner"]["id"] == str(world.owner.id)


@pytest.mark.parametrize(
    ("status", "expires", "expected"),
    [
        ("awaiting_review", timedelta(hours=1), True),
        ("awaiting_review", None, True),
        ("awaiting_review", timedelta(seconds=-5), False),
        ("approved", timedelta(hours=1), False),
        ("executed", None, False),
        ("blocked", None, False),
    ],
)
def test_can_decide_depends_on_status_and_expiry(
    world: World, status: str, expires: timedelta | None, expected: bool
) -> None:
    when = None if expires is None else datetime.now(UTC) + expires
    action = world.add(status=status, expires_at=when)
    assert world.get(world.owner, f"/v1/actions/{action}").json()["can_decide"] is expected


def test_decided_by_is_a_display_name_only(world: World) -> None:
    action = world.add(status="rejected", decided_by=world.admin.id, decided_at=datetime.now(UTC))
    body = world.get(world.owner, f"/v1/actions/{action}").json()
    assert set(body["decided_by"]) == {"id", "display_name"}
    assert world.admin.email not in json.dumps(body)


def test_retried_by_is_the_newest_visible_retry(world: World) -> None:
    first = world.add(status="blocked")
    world.add(retry_of=first, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    newest = world.add(retry_of=first, created_at=datetime(2026, 2, 1, tzinfo=UTC))
    assert world.get(world.owner, f"/v1/actions/{first}").json()["retried_by"] == str(newest)


def test_retried_by_ignores_a_retry_the_caller_cannot_see(world: World) -> None:
    first = world.add(status="blocked")
    world.add("other", retry_of=first)
    assert world.get(world.owner, f"/v1/actions/{first}").json()["retried_by"] is None


def test_a_failed_judge_shows_the_failure_and_no_verdict(world: World) -> None:
    judge = {
        "provider": "llm",
        "model": "m",
        "result": None,
        "failure": "llm_timeout",
        "reason_withheld": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": None,
        "duration_ms": 5,
    }
    action = world.add(judge=judge)
    shown = world.get(world.owner, f"/v1/actions/{action}").json()["judge"]
    assert shown["failure"] == "llm_timeout"
    assert shown["recommendation"] is None and shown["reason"] is None


def test_malformed_stored_json_is_a_500_in_the_envelope(
    world: World, auth_settings: Settings
) -> None:
    action = world.add(impact_report={"headline": "no other keys"})
    with TestClient(create_app(auth_settings), raise_server_exceptions=False) as quiet:
        response = quiet.get(f"/v1/actions/{action}", headers=token_for(world.owner))
    assert response.status_code == 500
    assert "no other keys" not in response.text


# --- nothing leaks ------------------------------------------------------------------------------


def test_raw_sql_and_emails_never_leave_the_backend(world: World) -> None:
    action = world.add(**full_action_columns(decided_by=world.admin.id))
    bodies = [
        world.get(world.owner, f"/v1/actions/{action}").text,
        world.get(world.owner, "/v1/actions").text,
        world.get(world.admin, f"/v1/actions/{action}").text,
    ]
    for text in bodies:
        assert RAW_SQL not in text and PINNED_SQL not in text
        assert "raw.secret" not in text and "jane.doe" not in text
        for email in (world.owner.email, world.other.email, world.admin.email):
            assert email not in text


def test_no_action_model_has_a_sql_or_credential_field(auth_settings: Settings) -> None:
    schemas = create_app(auth_settings).openapi()["components"]["schemas"]
    forbidden = {"sql", "pinned_sql", "email", "password", "api_key", "password_hash"}
    action_models = {
        "ActionDetail",
        "ActionSummary",
        "ActionPage",
        "AgentRef",
        "UserRef",
        "JudgeView",
        "SampleRowView",
        "KnowledgeRefView",
        "AuditEventView",
        "DecisionResponse",
        "RejectRequest",
    }
    assert action_models <= set(schemas)
    for name in action_models:
        assert not forbidden & set(schemas[name]["properties"]), name


# --- authentication -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/actions", f"/v1/actions/{uuid.uuid4()}"])
def test_no_or_bad_token_is_401(world: World, path: str) -> None:
    for headers in ({}, {"Authorization": "Bearer nonsense"}):
        response = world.client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("path", ["/v1/actions", f"/v1/actions/{uuid.uuid4()}"])
def test_deactivated_user_and_old_token_version_are_401(
    world: World, auth_db: AuthDb, path: str
) -> None:
    user = auth_db.make_user()
    headers = token_for(user)
    assert world.client.get(path, headers=headers).status_code != 401
    auth_db.execute("UPDATE users SET token_version = 1 WHERE id = %s", (user.id,))
    assert world.client.get(path, headers=headers).status_code == 401
    inactive = auth_db.make_user(is_active=False)
    assert world.client.get(path, headers=token_for(inactive)).status_code == 401


def test_database_down_answers_like_auth_me(world: World, auth_settings: Settings) -> None:
    dead_url = asyncpg_url("postgresql://blastproof_app:x@127.0.0.1:1/none")
    dead = Settings(
        _env_file=None,
        app_env="test",
        blastproof_database_url=dead_url,
        jwt_secret=TEST_JWT_SECRET,
    )
    with TestClient(create_app(dead), raise_server_exceptions=False) as client:
        responses = [
            client.get(path, headers=token_for(world.owner))
            for path in ("/v1/auth/me", "/v1/actions", f"/v1/actions/{uuid.uuid4()}")
        ]
    assert len({r.status_code for r in responses}) == 1
    assert len({json.dumps(r.json()) for r in responses}) == 1
    assert "127.0.0.1" not in responses[0].text and "postgresql" not in responses[0].text


# --- logs ---------------------------------------------------------------------------------------


def test_a_detail_request_logs_no_sql_or_intent(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    action = world.add(**full_action_columns())
    with caplog.at_level("DEBUG"):
        response = world.get(world.owner, f"/v1/actions/{action}")
    assert response.status_code == 200
    # caplog, not structlog's capture_logs: after configure_logging loggers are cached.
    text = json.dumps([str(r.msg) for r in caplog.records])
    assert caplog.records, "nothing was captured, so this test would prove nothing"
    assert "DELETE FROM" not in text and "Clean up" not in text and "raw.secret" not in text


def test_the_detail_route_binds_the_action_id(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    action = world.add()
    bound: dict[str, str] = {}
    real = structlog.contextvars.bind_contextvars

    def spy(**values: str) -> object:
        bound.update(values)
        return real(**values)

    monkeypatch.setattr(structlog.contextvars, "bind_contextvars", spy)
    world.get(world.owner, f"/v1/actions/{action}")
    assert bound.get("action_id") == str(action)


# --- done when ----------------------------------------------------------------------------------


def test_done_when_owner_reads_own_other_gets_404_admin_sees_both(world: World) -> None:
    mine = world.add(**full_action_columns())
    theirs = world.add("other", **full_action_columns())
    assert str(mine) in ids(world.get(world.owner, "/v1/actions"))
    assert world.get(world.owner, f"/v1/actions/{mine}").status_code == 200
    refused = world.get(world.other, f"/v1/actions/{mine}")
    assert refused.status_code == 404 and refused.json() == NOT_FOUND
    assert str(mine) not in ids(world.get(world.other, "/v1/actions"))
    both = set(ids(world.get(world.admin, "/v1/actions?limit=100")))
    assert {str(mine), str(theirs)} <= both
    for who in (world.owner, world.other, world.admin):
        for path in ("/v1/actions", f"/v1/actions/{mine}"):
            text = world.get(who, path).text
            assert RAW_SQL not in text and PINNED_SQL not in text


def test_page_model_matches_the_envelope() -> None:
    assert set(ActionPage.model_fields) == {"items", "total", "limit", "offset"}
